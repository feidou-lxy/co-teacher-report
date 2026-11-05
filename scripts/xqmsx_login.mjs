// 后台登录取 token —— 自己拉起 Chrome，用 CDP 直连，靠文件与外部接力。
//
// 为什么不用 agent-browser：它每条命令都起一个新浏览器实例，填表截图之后进程就没了，
// 「截验证码 → 人看一眼 → 回来提交」这条链跨调用接不上（每次重开验证码都换）。
// 这里由本进程自己 spawn Chrome，浏览器活到脚本结束，中间用
//   <OUT>/captcha.png      我截好给你的验证码图
//   <OUT>/captcha_code.txt 你把 4 位数字写进这个文件（我代写）
// 两个文件完成接力，不依赖任何跨调用状态。
//
// 用法（密码只走环境变量，不进命令行、不进日志）：
//   KB_USER=xxx KB_PASS=xxx KB_OUT=<workdir> node xqmsx_login.mjs
//
// 产出：<OUT>/token.txt（一行 token=…）+ stdout 的成功/失败原因。

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';

const CHROME = process.env.KB_CHROME ||
  '/Users/feidou/Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/' +
  'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
const PORT = Number(process.env.KB_PORT || 9333);
const OUT = process.env.KB_OUT || process.cwd();
const USER = process.env.KB_USER || '';
const PASS = process.env.KB_PASS || '';
const URL_LOGIN = 'https://stadminv2.xqmsx.cn/#/login';
const PROFILE = process.env.KB_PROFILE || '/tmp/xqmsx-login-profile';
const DEADLINE_MS = Number(process.env.KB_TIMEOUT || 900) * 1000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => {
      let b = '';
      r.on('data', (c) => (b += c));
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const wait = new Map();
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', () => res());
    ws.addEventListener('error', () => rej(new Error('CDP websocket error')));
  });
  ws.addEventListener('message', (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    if (m.id && wait.has(m.id)) {
      const { res, rej } = wait.get(m.id);
      wait.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    }
  });
  return {
    ready,
    ws,
    send: (method, params = {}) => new Promise((res, rej) => {
      const i = ++id;
      wait.set(i, { res, rej });
      ws.send(JSON.stringify({ id: i, method, params }));
    }),
  };
}

async function main() {
  if (!USER || !PASS) {
    console.error('缺少账号密码（要 KB_USER / KB_PASS 环境变量）');
    process.exit(2);
  }
  fs.mkdirSync(OUT, { recursive: true });
  const statePath = path.join(OUT, 'login_state.json');
  const codePath = path.join(OUT, 'captcha_code.txt');
  for (const p of [statePath, codePath]) { try { fs.unlinkSync(p); } catch {} }
  fs.writeFileSync(statePath, JSON.stringify({ stage: 'starting' }));

  const child = spawn(CHROME, [
    '--headless',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    '--no-first-run', '--no-default-browser-check', '--no-sandbox',
    // ⚠️ 必须带这两个，否则 macOS 每次都弹「想使用钥匙串中 Chromium Safe Storage 的机密信息」：
    //    Chrome for Testing 是临时/ad-hoc 签名，读钥匙串始终授权不持久，而我们反复 kill 重开 → 反复弹。
    //    mock keychain = 密钥只放内存，password-store=basic = 密码库不加密，两者都不碰系统钥匙串。
    '--use-mock-keychain', '--password-store=basic',
    '--no-proxy-server',          // v5.00：绕开系统代理（Clash 劫持会导致登录页 SPA 资源加载失败）
    '--disable-features=PasswordManagerOnboarding,AutofillServerCommunication',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,940',
    'about:blank',
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  // Chrome 起不来时错误只打在子进程 stderr 上，必须接住，否则只剩「没起来」四个字没法查
  const tail = [];
  // 实时打印 Chrome 自己的日志：起不来时只剩「没起来」四个字根本没法查
  child.stderr.on('data', (b) => {
    const s = String(b);
    for (const ln of s.split('\n')) if (ln.trim()) log('  [chrome]', ln.trim().slice(0, 160));
    tail.push(s); if (tail.length > 40) tail.shift();
  });
  child.stdout.on('data', (b) => { tail.push(String(b)); if (tail.length > 40) tail.shift(); });
  child.on('exit', (code, sig) => log('Chrome 子进程退出，code=', code, 'sig=', sig));

  const cleanup = () => { try { child.kill('SIGKILL'); } catch {} };
  process.on('exit', cleanup);
  process.on('SIGINT', () => { cleanup(); process.exit(1); });

  // 等 CDP 端口起来（冷启动 1~3 秒）
  // ⚠️ 除了 HTTP 探测，还要看 user-data-dir 里的 DevToolsActivePort：
  //    端口文件在 = Chrome 起来了但 localhost 连不上（网络受限）；端口文件不在 = Chrome 压根没起来。
  const activePortFile = path.join(PROFILE, 'DevToolsActivePort');
  let ver = null;
  for (let i = 0; i < 130; i++) {
    try { ver = await getJson(`http://127.0.0.1:${PORT}/json/version`); break; }
    catch (e) {
      if (i % 10 === 0) {
        let ap = '无';
        try { ap = fs.readFileSync(activePortFile, 'utf-8').split('\n')[0]; } catch {}
        log(`等待 CDP… pid=${child.pid} 端口文件=${ap} err=${e.code || e.message}`);
      }
      await sleep(300);
    }
  }
  if (!ver) {
    log('✗ Chrome 没起来（CDP 端口未响应）');
    log('子进程输出:', (tail.join('') || '(空)').slice(-800));
    process.exit(3);
  }
  log('浏览器已启动:', (ver.Browser || '').slice(0, 40));

  let page = null;
  for (let i = 0; i < 20; i++) {
    const list = await getJson(`http://127.0.0.1:${PORT}/json/list`).catch(() => []);
    page = (list || []).find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
    if (page) break;
    await sleep(300);
  }
  if (!page) { log('✗ 找不到 page target'); process.exit(3); }

  const c = cdp(page.webSocketDebuggerUrl);
  await c.ready;
  await c.send('Page.enable');
  await c.send('Runtime.enable');
  const ev = async (expr) => {
    const r = await c.send('Runtime.evaluate',
      { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };

  await c.send('Page.navigate', { url: URL_LOGIN });
  let hasForm = false;
  for (let i = 0; i < 60; i++) {
    hasForm = await ev('!!document.querySelector("input[type=password]")').catch(() => false);
    if (hasForm) break;
    await sleep(500);
  }
  if (!hasForm) { log('✗ 登录页没出现密码框'); process.exit(4); }
  log('登录页已加载');

  // 表单结构打一次，方便选择器变了能立刻看出来（不打印任何输入值）
  const formDump = await ev(`JSON.stringify([...document.querySelectorAll('input')]
    .map(el=>({type:el.type,name:el.name,ph:el.placeholder})))`);
  log('表单:', formDump);

  const fill = async (sel, val) => ev(`(()=>{
    const el=document.querySelector(${JSON.stringify(sel)});
    if(!el) return 'NO_EL';
    const d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
    d.set.call(el, ${JSON.stringify(val)});
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    return 'OK';
  })()`);

  const r1 = await fill("input[placeholder='请输入用户名']", USER);
  const r2 = await fill("input[type='password']", PASS);
  log('填账号:', r1, '| 填密码:', r2);

  // 截验证码（元素级，含内边距放大一点，避免边框裁掉字符）
  // ⚠️ 找不到 alt=请输入验证码 时**不要退回第一个 img** —— 登录页第一个 img 是吉祥物，
  //    截出来是一张卡通图，看着像验证码但不是，白等一轮（2026-11-03 实测踩中）。
  //    此时改为截整页，人工一眼就能看出验证码在哪、是不是改成了滑块。
  // 验证码图可能是填完表单后才异步加载的 → 先等它出现（最多 5 秒）
  for (let i = 0; i < 10; i++) {
    if (await ev(`!!document.querySelector("img[alt='请输入验证码']")`).catch(() => false)) break;
    await sleep(500);
  }
  const imgs = await ev(`JSON.stringify([...document.querySelectorAll('img')]
    .map(e=>({alt:e.alt,src:(e.src||'').slice(-40),w:Math.round(e.getBoundingClientRect().width)})))`);
  log('页面 img 列表:', imgs);
  const box = await ev(`(()=>{const el=document.querySelector("img[alt='请输入验证码']");
    if(!el) return null;
    const r=el.getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height};})()`);
  const shot = await c.send('Page.captureScreenshot', {
    format: 'png',
    ...(box && box.width > 10 ? {
      clip: { x: Math.max(0, box.x - 2), y: Math.max(0, box.y - 2),
              width: box.width + 4, height: box.height + 4, scale: 6 },
    } : {}),
  });
  const capPath = path.join(OUT, 'captcha.png');
  fs.writeFileSync(capPath, Buffer.from(shot.data, 'base64'));
  log('验证码已截图:', capPath, box ? `${Math.round(box.width)}x${Math.round(box.height)}` : '整页');
  fs.writeFileSync(statePath, JSON.stringify({ stage: 'wait_code', captcha: capPath }));

  // 等外部把 4 位数字写进 captcha_code.txt
  const t0 = Date.now();
  let code = '';
  while (Date.now() - t0 < DEADLINE_MS) {
    if (fs.existsSync(codePath)) {
      const raw = fs.readFileSync(codePath, 'utf-8').trim();
      if (/^\d{4}$/.test(raw)) { code = raw; break; }
      if (/^(reload|换一张|换)$/i.test(raw)) {           // 换码：重新截一张
        try { fs.unlinkSync(codePath); } catch {}
        const b2 = await ev(`(()=>{const el=document.querySelector("img[alt='请输入验证码']");
          if(!el) return null; el.click(); const r=el.getBoundingClientRect();
          return {x:r.x,y:r.y,width:r.width,height:r.height};})()`);
        await sleep(800);
        const s2 = await c.send('Page.captureScreenshot', {
          format: 'png',
          ...(b2 && b2.width > 10 ? { clip: { x: b2.x - 2, y: b2.y - 2,
            width: b2.width + 4, height: b2.height + 4, scale: 6 } } : {}),
        });
        fs.writeFileSync(capPath, Buffer.from(s2.data, 'base64'));
        log('已换一张验证码:', capPath);
      }
    }
    await sleep(800);
  }
  if (!code) { log('✗ 等验证码超时'); process.exit(5); }
  log('收到验证码（长度 ' + code.length + '）');

  const r3 = await fill("input[name='logVerify']", code);
  if (r3 === 'NO_EL') {
    // 属性可能改了：找紧挨着验证码图片的那个 input
    const alt = await ev(`(()=>{const img=document.querySelector("img[alt='请输入验证码']")
        || document.querySelector("img");
      if(!img) return null; let n=img;
      for(let i=0;i<6&&n;i++){ n=n.parentElement;
        const inp=n&&n.querySelector('input'); if(inp) return inp.name||inp.placeholder||'?'; }
      return null;})()`);
    log('⚠️ 找不到 name=logVerify，附近 input:', alt);
    if (alt && alt !== '?') await fill(`input[name='${alt}']`, code);
  }
  await ev(`(()=>{const b=document.querySelector("button.el-button--primary:not(.is-link)")
    || [...document.querySelectorAll('button')].find(x=>/登\\s*录/.test(x.innerText));
    if(b){b.click(); return 'clicked';} return 'NO_BTN';})()`)
    .then((x) => log('点击登录:', x));

  // 等跳转 / cookie 出现
  let ck = '', href = '';
  for (let i = 0; i < 30; i++) {
    await sleep(700);
    ck = await ev('document.cookie').catch(() => '');
    href = await ev('location.href').catch(() => '');
    if (/token=/i.test(ck) || !/#\/login/.test(href)) break;
  }
  const m = ck.match(/(?:^|;\s*)(token=[^;]+)/i);
  if (!m) {
    const tip = await ev('document.body.innerText').catch(() => '');
    log('✗ 登录未成功；页面提示：', (tip || '').replace(/\s+/g, ' ').slice(0, 120));
    log('  url=', href, '| cookie 里没有 token');
    process.exit(6);
  }
  const tok = m[1];
  fs.writeFileSync(path.join(OUT, 'token.txt'), tok + '\n');
  fs.writeFileSync(statePath, JSON.stringify({ stage: 'done', href }));
  log('✓ 已写入', path.join(OUT, 'token.txt'), '长度', tok.length);
  cleanup();
  process.exit(0);
}

main().catch((e) => { log('✗ 异常:', e.message); process.exit(9); });
