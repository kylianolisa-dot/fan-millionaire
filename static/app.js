// Fan Millionaire single-page app (hash routing). Black / gold / white.
(() => {
  const S = { me: null, content: {} };
  let stream = null, timer = null;
  const root = document.getElementById('root');
  let mainEl;

  // ------------------------------------------------------------ layout
  function layout(active) {
    const c = S.content, me = S.me;
    const link = (href, label, cls = '') => h('a', { href, class: (active === href ? 'on ' : '') + cls }, label);
    const nav = h('nav', {}, h('div', { class: 'wrap' },
      h('a', { href: '#/', class: 'logo' }, c.site_name || 'Fan Millionaire'),
      h('div', { class: 'spacer' }),
      h('div', { class: 'navlinks' },
        link('#/', 'Matches'), link('#/page/rules', 'Rules'),
        me ? [link('#/wallet', naira(me.balance_kobo), 'walletchip'), link('#/profile', me.name.split(' ')[0]),
              me.role === 'admin' ? h('a', { href: '/admin.html' }, 'Admin') : null]
           : [link('#/login', 'Log in'), h('a', { href: '#/signup', class: 'btn small' }, 'Sign up')])));
    mainEl = h('main', {}, h('div', { class: 'wrap', id: 'view' }));
    root.replaceChildren(
      c.banner ? h('div', { class: 'banner' }, c.banner) : null, nav, mainEl,
      h('footer', {}, h('div', { class: 'wrap' }, c.footer_text || '')));
    return document.getElementById('view');
  }

  async function refreshMe() {
    try { S.me = (await api('/api/me')).user; } catch (_) { S.me = null; }
  }

  function closeStream() {
    if (stream) { stream.close(); stream = null; }
    if (timer) { clearInterval(timer); timer = null; }
  }

  // ------------------------------------------------------------ router
  async function route() {
    closeStream();
    const parts = (location.hash || '#/').slice(2).split('/');
    const [a, b] = parts;
    await refreshMe();
    try {
      if (!a) return home();
      if (a === 'match') return matchPage(Number(b));
      if (a === 'wallet') return walletPage();
      if (a === 'profile') return profilePage();
      if (a === 'login') return authPage(false);
      if (a === 'signup') return authPage(true);
      if (a === 'page') return contentPage(b);
      home();
    } catch (e) { layout('').replaceChildren(h('div', { class: 'card' }, e.message)); }
  }

  // ------------------------------------------------------------ home
  function potBar(m) {
    const p = m.pots, t = p.total || 1;
    return h('div', {},
      h('div', { class: 'potbar' }, h('i', { class: 'h', style: `width:${(p.home / t) * 100}%` }), h('i', { class: 'a', style: `width:${(p.away / t) * 100}%` })),
      h('div', { class: 'potnums' }, h('span', {}, h('b', {}, naira(p.home)), ` · ${p.home_fans} fans`), h('span', {}, `${p.away_fans} fans · `, h('b', {}, naira(p.away)))));
  }

  function statusChip(m) {
    if (m.status === 'cancelled') return h('span', { class: 'chip bad' }, 'Cancelled');
    if (m.status === 'open') return m.staking_open ? h('span', { class: 'chip ok' }, 'Staking open') : h('span', { class: 'chip' }, 'Staking closed');
    const g = m.game;
    if (g && g.status === 'running') return h('span', { class: 'chip gold' }, 'Trivia live');
    if (g && (g.status === 'pending' || g.status === 'interrupted')) return h('span', { class: 'chip gold' }, 'Trivia soon');
    return h('span', { class: 'chip' }, 'Final');
  }

  function matchCard(m) {
    const fin = m.status === 'finished';
    return h('a', { class: 'mcard', href: '#/match/' + m.id },
      h('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:10px' },
        h('span', { class: 'muted' }, m.competition || fmtDate(m.kickoff_at)), statusChip(m)),
      h('div', { class: 'teams' }, h('div', { class: 'team' }, m.home),
        fin ? h('div', { class: 'score' }, `${m.home_score} – ${m.away_score}`) : h('div', { class: 'vs' }, 'VS'),
        h('div', { class: 'team' }, m.away)),
      potBar(m),
      h('div', { class: 'row', style: 'justify-content:space-between;margin-top:6px' },
        h('span', { class: 'muted' }, fmtDate(m.kickoff_at)),
        h('span', {}, 'Total pot ', h('b', { class: 'gold' }, naira(m.pots.total)))));
  }

  async function home() {
    const v = layout('#/'), c = S.content;
    const { open, finished } = await api('/api/matches');
    v.replaceChildren(
      h('section', { class: 'hero' },
        c.hero_image_url ? h('img', { src: c.hero_image_url, alt: '' }) : null,
        h('h1', {}, c.hero_title), h('p', {}, c.hero_text),
        S.me ? null : h('a', { href: '#/signup', class: 'btn' }, 'Join Fan Millionaire')),
      h('h2', {}, 'Upcoming matches'),
      open.length ? h('div', { class: 'grid two' }, open.map(matchCard)) : h('p', { class: 'muted' }, 'No matches open right now. Check back soon.'),
      finished.length ? [h('h2', { style: 'margin-top:34px' }, 'Recent results'), h('div', { class: 'grid two' }, finished.map(matchCard))] : null,
      h('div', { class: 'card', style: 'margin-top:34px' }, h('h2', { class: 'gold' }, c.how_title), h('div', { class: 'prose' }, paragraphs(c.how_text))));
  }

  function contentPage(slug) {
    const c = S.content, map = { rules: 'rules', faq: 'faq', terms: 'terms' };
    const k = map[slug] || 'rules';
    const v = layout('#/page/' + k);
    v.replaceChildren(h('div', { class: 'card' }, h('h1', { class: 'gold', style: 'font-size:2rem' }, c[k + '_title']),
      h('div', { class: 'prose' }, paragraphs(c[k + '_text'])),
      h('p', {}, h('a', { href: '#/page/rules' }, 'Rules'), ' · ', h('a', { href: '#/page/faq' }, 'FAQ'), ' · ', h('a', { href: '#/page/terms' }, 'Terms'))));
  }

  // ------------------------------------------------------------ auth
  function authPage(signup) {
    const v = layout(signup ? '#/signup' : '#/login');
    const f = {};
    const field = (key, label, type = 'text', ac = '') => [h('label', {}, label), (f[key] = h('input', { type, autocomplete: ac }))];
    const age = h('input', { type: 'checkbox' });
    const err = h('p', { class: 'muted', style: 'color:var(--bad)' });
    const btn = h('button', { class: 'btn', style: 'width:100%;margin-top:6px' }, signup ? 'Create account' : 'Log in');
    btn.addEventListener('click', async () => {
      err.textContent = ''; btn.disabled = true;
      try {
        const data = { email: f.email.value, password: f.password.value, device_id: deviceId() };
        if (signup) Object.assign(data, { name: f.name.value, phone: f.phone.value, age_confirmed: age.checked });
        await api(signup ? '/api/signup' : '/api/login', 'POST', data);
        location.hash = '#/';
        route();
      } catch (e) { err.textContent = e.message; }
      btn.disabled = false;
    });
    v.replaceChildren(h('div', { class: 'card gold', style: 'max-width:440px;margin:20px auto' },
      h('h2', {}, signup ? 'Create your account' : 'Welcome back'),
      signup ? field('name', 'Full name', 'text', 'name') : null,
      field('email', 'Email', 'email', 'email'),
      signup ? field('phone', 'Phone number', 'tel', 'tel') : null,
      field('password', 'Password' + (signup ? ' (8+ characters)' : ''), 'password', signup ? 'new-password' : 'current-password'),
      signup ? h('label', { class: 'check' }, age, h('span', {}, 'I am 18 or older and I accept the ', h('a', { href: '#/page/terms' }, 'terms'), '.')) : null,
      err, btn,
      h('p', { class: 'muted' }, signup ? 'Already have an account? ' : 'New here? ',
        h('a', { href: signup ? '#/login' : '#/signup' }, signup ? 'Log in' : 'Sign up'))));
  }

  // ------------------------------------------------------------ match page + live trivia
  function matchPage(id) {
    const v = layout('#/');
    let M = null, G = null, answered = false, pick = null, offset = 0;
    const head = h('div'), potsEl = h('div'), stakeEl = h('div'), stageEl = h('div'), winEl = h('div');
    v.replaceChildren(head, potsEl, stakeEl, stageEl, winEl);
    const club = (side) => (side === 'home' ? M.home : M.away);
    const now = () => Date.now() + offset;

    function paintHead() {
      const fin = M.status === 'finished';
      const outcome = !fin ? '' : M.outcome === 'draw' ? 'The match ended in a draw.' : `${club(M.outcome)} won.`;
      head.replaceChildren(h('div', { class: 'card', style: 'text-align:center' },
        h('div', { class: 'row', style: 'justify-content:space-between' }, h('a', { href: '#/' }, '← All matches'), statusChip(M)),
        h('div', { class: 'teams', style: 'margin:14px 0' }, h('div', { class: 'team', style: 'font-size:1.5rem' }, M.home),
          fin ? h('div', { class: 'score' }, `${M.home_score} – ${M.away_score}`) : h('div', { class: 'vs' }, 'VS'),
          h('div', { class: 'team', style: 'font-size:1.5rem' }, M.away)),
        h('div', { class: 'muted' }, (M.competition ? M.competition + ' · ' : '') + fmtDate(M.kickoff_at)),
        outcome ? h('p', { class: 'gold', style: 'font-weight:700' }, outcome) : null));
    }

    function paintPots() {
      const p = M.pots;
      const one = (side) => h('div', { class: 'card potcard' }, h('div', { class: 'club' }, club(side)),
        h('div', { class: 'amt' }, naira(p[side])), h('div', { class: 'muted' }, `${p[side + '_fans']} fans backing`));
      potsEl.replaceChildren(h('div', { style: 'margin:16px 0' },
        h('div', { class: 'grid two', style: 'grid-template-columns:1fr 1fr' }, one('home'), one('away')),
        h('div', { style: 'text-align:center;margin-top:12px' }, h('span', { class: 'muted' }, 'Total pot'), h('div', { class: 'bigpot' }, naira(p.total)))));
    }

    function paintStake() {
      if (M.status === 'cancelled') return stakeEl.replaceChildren(h('div', { class: 'card' }, 'This match was cancelled. All stakes were refunded to wallets.'));
      if (M.status === 'finished') {
        const me = M.me;
        if (!S.me) return stakeEl.replaceChildren();
        return stakeEl.replaceChildren(h('div', { class: 'card' }, me && me.side
          ? [h('b', {}, `You backed ${club(me.side)} with ${naira(me.staked_kobo)}. `),
             me.can_play ? h('span', { class: 'gold' }, 'You are in the trivia. Be ready!') : h('span', { class: 'muted' }, 'You are not playing the remaining trivia rounds.')]
          : h('span', { class: 'muted' }, 'You did not stake on this match, so you cannot play its trivia.')));
      }
      if (!M.staking_open) return stakeEl.replaceChildren(h('div', { class: 'card' }, 'Staking is closed. Waiting for the final whistle and result.'));
      if (!S.me) return stakeEl.replaceChildren(h('div', { class: 'card gold', style: 'text-align:center' },
        h('p', {}, 'Log in to back your club.'), h('a', { class: 'btn', href: '#/login' }, 'Log in'), ' ', h('a', { class: 'btn ghost', href: '#/signup' }, 'Sign up')));
      let side = M.me && M.me.side ? M.me.side : null;
      const amt = h('input', { type: 'number', min: '100', step: '50', placeholder: 'Amount in ₦' });
      const sideBtns = ['home', 'away'].map((s) => {
        const b = h('button', { disabled: !!(M.me && M.me.side && M.me.side !== s) }, club(s));
        b.addEventListener('click', () => { side = s; sideBtns.forEach((x, i) => x.classList.toggle('on', ['home', 'away'][i] === s)); });
        if (side === s) b.classList.add('on');
        return b;
      });
      const go = h('button', { class: 'btn', style: 'width:100%' }, 'Put it in the pot');
      go.addEventListener('click', async () => {
        if (!side) return toast('Pick a club first', 'bad');
        const naira_ = Number(amt.value);
        if (!naira_ || naira_ <= 0) return toast('Enter an amount', 'bad');
        go.disabled = true;
        try {
          const r = await api(`/api/matches/${id}/stake`, 'POST', { side, amount_kobo: Math.round(naira_ * 100) });
          M.pots = r.pots; S.me.balance_kobo = r.balance_kobo;
          toast(`${naira(Math.round(naira_ * 100))} added to ${club(side)}'s pot`);
          amt.value = ''; await refreshMe(); closeStream(); matchPage(id); return;
        } catch (e) { toast(e.message, 'bad'); }
        go.disabled = false;
      });
      const chips = [500, 1000, 2000, 5000].map((n) => h('button', { onclick: () => { amt.value = n; } }, '₦' + n.toLocaleString()));
      stakeEl.replaceChildren(h('div', { class: 'card gold' },
        h('h3', {}, 'Back your club'),
        M.me && M.me.side ? h('p', { class: 'muted' }, `You have ${naira(M.me.staked_kobo)} on ${club(M.me.side)}. You can add more to the same club.`) : null,
        h('div', { class: 'sidepick' }, sideBtns), h('label', {}, 'Amount (₦)'), amt, h('div', { class: 'chips' }, chips),
        h('div', { class: 'row', style: 'justify-content:space-between;margin:8px 0 12px' },
          h('span', { class: 'muted' }, 'Wallet: ' + naira(S.me.balance_kobo)), h('a', { href: '#/wallet' }, 'Deposit')), go,
        h('p', { class: 'muted', style: 'font-size:.85rem' }, 'You must stake to play the trivia. You can only back one club per match.')));
    }

    function paintWinners() {
      const w = M.winners || [];
      if (!w.length) return winEl.replaceChildren();
      winEl.replaceChildren(h('div', { class: 'card', style: 'margin-top:16px' }, h('h3', { class: 'gold' }, 'Prize winners'),
        w.map((x) => h('div', { class: 'winrow' }, h('span', {}, `Round ${x.round_no} · `, h('b', {}, x.name), ` (${club(x.side)})`),
          h('span', {}, h('b', {}, naira(x.amount_kobo)), ` · ${(x.answer_ms / 1000).toFixed(2)}s`)))));
    }

    function canAnswer() { return !!(S.me && M.me && M.me.can_play && G && G.sides && G.sides.includes(M.me.side)); }

    function paintStage() {
      const g = M.game;
      if (M.status !== 'finished') return stageEl.replaceChildren(h('div', { class: 'card', style: 'margin-top:16px;text-align:center' },
        h('h3', { class: 'gold' }, 'Trivia unlocks after the final whistle'),
        h('p', { class: 'muted' }, 'Fans who staked on the winning club play 3 rounds for the pot. In a draw, fans of both clubs play.')));
      if (!g || g.mode === 'none') return stageEl.replaceChildren(h('div', { class: 'card', style: 'margin-top:16px' }, 'Nobody staked on this match, so there is no trivia.'));
      const box = (...k) => stageEl.replaceChildren(h('div', { class: 'stage', style: 'margin-top:16px' }, ...k));
      const mode = g.mode === 'draw' ? 'Draw: the fastest correct fan from each club wins a prize each round.' : `${club(g.play_side)} fans play. The fastest correct fan each round wins.`;
      if (g.status === 'done') return box(h('h2', {}, 'Trivia is over'), h('p', { class: 'muted' }, 'See the prize winners below.'));
      if (g.status === 'interrupted') return box(h('h2', {}, 'Trivia paused'), h('p', {}, 'We hit a problem and will restart the trivia shortly. Stay on this page.'));
      if (!G) {
        return box(h('h2', {}, 'Trivia starts in'), h('div', { class: 'countdown', id: 'pre' }, '--'), h('p', { class: 'muted' }, mode),
          M.slot_amount_kobo ? h('span', { class: 'prize' }, naira(M.slot_amount_kobo) + ' per prize') : null,
          h('p', { class: 'muted' }, 'Keep this page open. Only fans who staked on an eligible club can answer.'));
      }
      const prizes = (G.prizes || []).map((p) => h('span', { class: 'prize' }, `${club(p.side)} fans · ${naira(p.amount)}`));
      const title = `Round ${G.round_no || 1} of 3` + (G.attempt ? ` · retake ${G.attempt}` : '');
      if (G.phase === 'starting') return box(h('h2', {}, 'Trivia is starting…'), h('p', { class: 'muted' }, mode));
      if (G.phase === 'countdown') return box(h('h2', {}, title), h('div', {}, prizes), h('p', { class: 'muted' }, 'Get ready. The question appears for everyone at the same moment.'),
        h('div', { class: 'countdown', id: 'cd' }, '--'), h('p', { class: 'muted' }, `${G.eligible_count} fans eligible`),
        canAnswer() ? h('p', { class: 'gold' }, 'You are eligible. Good luck!') : h('p', { class: 'muted' }, 'You are watching this round.'));
      if (G.phase === 'open' || G.phase === 'closing') {
        const can = canAnswer() && !answered, q = G.question;
        return box(h('div', { class: 'muted' }, title), h('div', {}, prizes),
          h('div', { class: 'timerbar', style: 'margin-top:12px' }, h('i', { id: 'bar' })),
          h('div', { class: 'qtext' }, q.text),
          h('div', { class: 'opts' }, q.options.map((o, i) => h('button', { class: 'opt' + (pick === i ? ' picked' : ''), disabled: !can, onclick: () => submit(i) }, o))),
          answered ? h('p', { class: 'gold' }, 'Answer locked in ✓') : canAnswer() ? h('p', { class: 'muted' }, 'Tap your answer. Only your first tap counts.') : h('p', { class: 'muted' }, 'You are watching this round.'),
          h('p', { class: 'muted', id: 'left' }));
      }
      if (G.phase === 'result' && G.last_result) {
        const r = G.last_result, q = G.question;
        return box(h('h2', {}, `Round ${r.round_no} result`),
          q ? h('div', { class: 'opts', style: 'margin:10px auto' }, q.options.map((o, i) => h('div', { class: 'opt' + (i === r.correct_index ? ' right' : pick === i ? ' wrong' : '') }, o))) : null,
          r.winners.length ? r.winners.map((w) => h('div', { class: 'winrow' }, h('span', {}, h('b', {}, w.name), ` (${club(w.side)})`), h('span', {}, h('b', {}, naira(w.amount)), ` · ${(w.ms / 1000).toFixed(2)}s`)))
            : h('p', { class: 'muted' }, 'Nobody answered correctly.'),
          r.unfilled.length && r.winners.length ? h('p', { class: 'muted' }, `${r.unfilled.map(club).join(' & ')} fans retake this round with a new question.`) : null,
          !r.winners.length ? h('p', { class: 'muted' }, 'This round will be retaken with a new question.') : null);
      }
      box(h('h2', {}, 'Trivia in progress'));
    }

    async function submit(i) {
      if (answered || !G || G.phase !== 'open') return;
      answered = true; pick = i; paintStage();
      try { await api('/api/answer', 'POST', { match_id: id, round_id: G.round_id, option: i }); }
      catch (e) { toast(e.message, 'bad'); }
    }

    function tick() {
      const t = now();
      const pre = document.getElementById('pre');
      if (pre && M.trivia_start_at) {
        const s = Math.max(0, Math.ceil(M.trivia_start_at - t / 1000));
        pre.textContent = s > 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s > 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : s + 's';
      }
      const cd = document.getElementById('cd');
      if (cd && G) cd.textContent = Math.max(0, Math.ceil((G.reveal_ms - t) / 1000));
      const bar = document.getElementById('bar');
      if (bar && G) {
        const total = G.ends_ms - G.reveal_ms, left = G.ends_ms - t;
        bar.style.width = Math.max(0, Math.min(100, (left / total) * 100)) + '%';
        const l = document.getElementById('left');
        if (l) l.textContent = left > 0 ? `${(left / 1000).toFixed(1)}s left` : 'Checking answers…';
      }
    }

    async function refresh() {
      const m = await api('/api/matches/' + id);
      M = m; paintHead(); paintPots(); paintWinners(); paintStake(); paintStage();
    }

    const first = async () => {
      try { M = await api('/api/matches/' + id); } catch (e) { return v.replaceChildren(h('div', { class: 'card' }, e.message)); }
      paintHead(); paintPots(); paintStake(); paintStage(); paintWinners();
      stream = new EventSource(`/api/matches/${id}/stream`);
      const on = (ev, fn) => stream.addEventListener(ev, (e) => fn(JSON.parse(e.data)));
      on('snapshot', (d) => { offset = d.server_ms - Date.now(); M = d.match; G = d.state; answered = d.answered; paintHead(); paintPots(); paintWinners(); paintStake(); paintStage(); });
      on('ping', (d) => { api('/api/pong', 'POST', { nonce: d.nonce }).catch(() => {}); });   // lets the server measure our latency
      on('pots', (p) => { M.pots = p; paintPots(); });
      on('match_finished', () => refresh());
      on('game_start', () => { M.game = Object.assign({}, M.game, { status: 'running' }); G = { phase: 'starting' }; paintStage(); });
      on('intro', (d) => { offset = d.server_ms - Date.now(); G = Object.assign({}, d, { phase: 'countdown' }); answered = false; pick = null; paintStage(); });
      on('question', (d) => { offset = d.server_ms - Date.now(); G = Object.assign({}, G, d, { phase: 'open' }); paintStage(); });
      on('round_result', (r) => { G = Object.assign({}, G, { phase: 'result', last_result: r }); paintStage(); refresh().then(() => { G = Object.assign({}, G); }); });
      on('round_skipped', () => {});
      on('game_over', () => { G = null; M.game = Object.assign({}, M.game, { status: 'done' }); refresh(); });
      on('game_error', () => { M.game = Object.assign({}, M.game, { status: 'interrupted' }); G = null; paintStage(); });
      timer = setInterval(tick, 100);
    };
    first();
  }

  // ------------------------------------------------------------ wallet
  async function walletPage() {
    if (!S.me) { location.hash = '#/login'; return; }
    const v = layout('#/wallet');
    const w = await api('/api/wallet');
    const dep = h('input', { type: 'number', min: '100', placeholder: 'Amount in ₦' });
    const depBtn = h('button', { class: 'btn', style: 'width:100%' }, 'Deposit with Paystack');
    depBtn.addEventListener('click', async () => {
      const n = Number(dep.value);
      if (!n) return toast('Enter an amount', 'bad');
      depBtn.disabled = true;
      try { const r = await api('/api/wallet/deposit', 'POST', { amount_kobo: Math.round(n * 100) }); location.href = r.authorization_url; }
      catch (e) { toast(e.message, 'bad'); depBtn.disabled = false; }
    });
    const bank = w.bank;
    const banks = h('select', {}, h('option', { value: '' }, 'Select bank'));
    api('/api/banks').then((r) => r.banks.forEach((b) => banks.append(h('option', { value: b.code }, b.name)))).catch(() => {});
    const acct = h('input', { type: 'text', inputmode: 'numeric', maxlength: '10', placeholder: '10-digit account number' });
    const saveBank = h('button', { class: 'btn ghost', style: 'width:100%;margin-top:10px' }, 'Verify & save account');
    saveBank.addEventListener('click', async () => {
      if (!banks.value) return toast('Select your bank', 'bad');
      saveBank.disabled = true;
      try { const r = await api('/api/wallet/bank', 'POST', { bank_code: banks.value, bank_name: banks.selectedOptions[0].text, account_number: acct.value });
        toast('Account verified: ' + r.account_name); walletPage(); }
      catch (e) { toast(e.message, 'bad'); }
      saveBank.disabled = false;
    });
    const wd = h('input', { type: 'number', min: '100', placeholder: 'Amount in ₦' });
    const wdBtn = h('button', { class: 'btn', style: 'width:100%' }, 'Withdraw to bank');
    wdBtn.disabled = !bank.has_bank;
    wdBtn.addEventListener('click', async () => {
      const n = Number(wd.value);
      if (!n) return toast('Enter an amount', 'bad');
      wdBtn.disabled = true;
      try { const r = await api('/api/wallet/withdraw', 'POST', { amount_kobo: Math.round(n * 100) });
        toast(r.status === 'pending_approval' ? 'Withdrawal requested. It will be reviewed shortly.' : 'Withdrawal on its way'); walletPage(); }
      catch (e) { toast(e.message, 'bad'); wdBtn.disabled = false; }
    });
    const label = { deposit: 'Deposit', stake: 'Stake', prize: 'Prize won', withdrawal: 'Withdrawal', withdrawal_refund: 'Withdrawal refunded', refund: 'Stake refunded' };
    v.replaceChildren(
      w.mock ? h('div', { class: 'card', style: 'margin-bottom:16px' }, h('span', { class: 'chip gold' }, 'Test mode'), ' No real money moves until Paystack keys are added.') : null,
      h('div', { class: 'card gold', style: 'text-align:center;margin-bottom:16px' }, h('div', { class: 'muted' }, 'Wallet balance'), h('div', { class: 'bigpot' }, naira(w.balance_kobo))),
      h('div', { class: 'grid two' },
        h('div', { class: 'card' }, h('h3', {}, 'Deposit'), h('label', {}, 'Amount (₦)'), dep, h('div', { class: 'chips' }, [1000, 2000, 5000, 10000].map((n) => h('button', { onclick: () => { dep.value = n; } }, '₦' + n.toLocaleString()))), depBtn),
        h('div', { class: 'card' }, h('h3', {}, 'Withdraw'),
          bank.has_bank ? h('p', {}, `Paying to ${bank.bank_name} · ${bank.account_number}`, h('br'), h('span', { class: 'muted' }, bank.account_name))
            : h('p', { class: 'muted' }, 'Add your bank account first.'),
          h('label', {}, 'Amount (₦). Minimum ' + naira(w.min_withdraw_kobo)), wd, h('div', { style: 'height:10px' }), wdBtn,
          h('details', { style: 'margin-top:14px' }, h('summary', { class: 'muted', style: 'cursor:pointer' }, bank.has_bank ? 'Change bank account' : 'Add bank account'),
            h('label', {}, 'Bank'), banks, h('label', {}, 'Account number'), acct, saveBank))),
      h('div', { class: 'card', style: 'margin-top:16px' }, h('h3', {}, 'History'),
        table(['When', 'What', 'Amount', 'Note'], w.ledger.map((l) => [fmtDate(l.created_at), label[l.type] || l.type, (l.amount_kobo > 0 ? '+' : '') + naira(l.amount_kobo), l.note || '']))));
  }

  // ------------------------------------------------------------ profile
  async function profilePage() {
    if (!S.me) { location.hash = '#/login'; return; }
    const v = layout('#/profile');
    const hist = await api('/api/me/history');
    const name = h('input', { value: S.me.name }), phone = h('input', { value: S.me.phone || '' });
    const save = h('button', { class: 'btn', style: 'margin-top:12px' }, 'Save profile');
    save.addEventListener('click', async () => {
      try { await api('/api/me', 'PUT', { name: name.value, phone: phone.value }); toast('Profile saved'); route(); }
      catch (e) { toast(e.message, 'bad'); }
    });
    const out = h('button', { class: 'btn ghost', style: 'margin-left:10px' }, 'Log out');
    out.addEventListener('click', async () => { await api('/api/logout', 'POST'); S.me = null; location.hash = '#/'; route(); });
    v.replaceChildren(
      h('div', { class: 'card' }, h('h2', {}, 'Your profile'), h('p', { class: 'muted' }, S.me.email),
        h('label', {}, 'Name'), name, h('label', {}, 'Phone'), phone, save, out),
      h('div', { class: 'card', style: 'margin-top:16px' }, h('h3', {}, 'My stakes'),
        table(['Match', 'Backed', 'Amount'], hist.stakes.map((s) => [h('a', { href: '#/match/' + s.match_id }, `${s.home} v ${s.away}`), s.side === 'home' ? s.home : s.away, naira(s.amount_kobo)]))),
      h('div', { class: 'card', style: 'margin-top:16px' }, h('h3', { class: 'gold' }, 'My wins'),
        table(['Match', 'Round', 'Prize', 'Time'], hist.wins.map((w) => [`${w.home} v ${w.away}`, w.round_no, naira(w.amount_kobo), (w.answer_ms / 1000).toFixed(2) + 's']))));
  }

  // ------------------------------------------------------------ boot
  (async () => {
    try { S.content = await api('/api/content'); document.title = S.content.site_name; } catch (_) {}
    window.addEventListener('hashchange', route);
    route();
  })();
})();
