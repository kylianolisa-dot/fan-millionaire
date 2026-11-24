// Fan Millionaire admin panel
(() => {
  const root = document.getElementById('root');
  const TABS = [['dash', 'Dashboard'], ['matches', 'Matches & pots'], ['feed', 'Fixtures & results'], ['wins', 'Prize wins'], ['unclaimed', 'Unclaimed funds'],
    ['users', 'Users'], ['withdrawals', 'Withdrawals'], ['questions', 'Trivia questions'], ['content', 'Website content'],
    ['settings', 'Settings'], ['flags', 'Anti-cheat flags']];
  let tab = 'dash', panel;

  const toEpoch = (v) => Math.floor(new Date(v).getTime() / 1000);
  const club = (m, side) => (side === 'home' ? m.home : m.away);
  const statusChip = (s) => h('span', { class: 'chip ' + (['success', 'done', 'won', 'active', 'finished'].includes(s) ? 'ok' : ['failed', 'declined', 'suspended', 'cancelled', 'interrupted', 'needs_attention'].includes(s) ? 'bad' : 'gold') }, s);
  const reason = { no_eligible_players: 'No eligible player left', no_correct_answer: 'Nobody answered correctly after retakes', no_stakers_on_side: 'Club had no stakers' };
  const run = async (fn, ok) => { try { await fn(); if (ok) toast(ok); } catch (e) { toast(e.message, 'bad'); } };

  // ---------------------------------------------------------- shell
  function shell(me) {
    panel = h('div', { class: 'panel' });
    root.replaceChildren(
      h('div', { class: 'top' }, h('div', { class: 'wrap' }, h('span', { class: 'logo' }, 'Fan Millionaire ', h('span', {}, 'Admin')), h('div', { class: 'spacer' }),
        h('a', { href: '/', style: 'color:#fff' }, 'View site'), h('button', { class: 'btn small ghost', style: 'color:#fff', onclick: async () => { await api('/api/logout', 'POST'); location.reload(); } }, 'Log out'))),
      h('div', { class: 'layout' }, h('div', { class: 'side' }, TABS.map(([k, label]) =>
        h('button', { class: k === tab ? 'on' : '', onclick: () => { tab = k; shell(me); } }, label))), panel));
    load();
  }

  async function load() {
    if (VIEWS._t) { clearInterval(VIEWS._t); VIEWS._t = null; }
    panel.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
    try { await VIEWS[tab](); } catch (e) { panel.replaceChildren(h('p', {}, e.message)); }
  }
  const show = (...k) => panel.replaceChildren(...k);

  // ---------------------------------------------------------- views
  const VIEWS = {
    async dash() {
      const d = await api('/api/admin/dashboard');
      const tile = (label, val, gold) => h('div', { class: 'tile' + (gold ? ' gold' : '') }, h('div', { class: 'muted' }, label), h('div', { class: 'stat' }, val));
      show(h('h2', {}, 'Dashboard'),
        d.mock_payments ? h('p', {}, h('span', { class: 'chip gold' }, 'Test mode'), ' Paystack keys are not set. No real money is moving.') : null,
        h('div', { class: 'grid tiles' },
          tile('Platform earnings (fees + unclaimed)', naira(d.platform_total_kobo), true), tile('Platform share of pots', naira(d.platform_fee_kobo)),
          tile('Unclaimed funds kept', naira(d.unclaimed_kobo)), tile('Prizes paid to fans', naira(d.prizes_paid_kobo)),
          tile('Total staked', naira(d.staked_kobo)), tile('Total deposited', naira(d.deposits_kobo)),
          tile('Fan wallet balances (owed)', naira(d.wallet_liability_kobo)), tile('Fans', d.users),
          tile('Open matches', d.open_matches), tile('Games running now', d.running_games),
          tile('Withdrawals to review', `${d.pending_withdrawals.n} · ${naira(d.pending_withdrawals.t)}`), tile('Open anti-cheat flags', d.open_flags)));
    },

    async matches() {
      const { matches } = await api('/api/admin/matches');
      const k = { home: h('input'), away: h('input'), comp: h('input'), ko: h('input', { type: 'datetime-local' }), close: h('input', { type: 'datetime-local' }) };
      const add = h('button', { class: 'btn' }, 'Create match');
      add.addEventListener('click', () => run(async () => {
        await api('/api/admin/matches', 'POST', { home: k.home.value, away: k.away.value, competition: k.comp.value,
          kickoff_at: toEpoch(k.ko.value), staking_closes_at: toEpoch(k.close.value || k.ko.value) });
        load();
      }, 'Match created'));
      const rows = matches.map((m) => {
        const hs = h('input', { type: 'number', min: '0', style: 'width:64px', placeholder: m.home.slice(0, 3) });
        const as = h('input', { type: 'number', min: '0', style: 'width:64px', placeholder: m.away.slice(0, 3) });
        const acts = [h('button', { class: 'btn small ghost', onclick: () => detail(m.id) }, 'Details')];
        if (m.status === 'open') {
          acts.unshift(h('div', { class: 'row' }, hs, '–', as, h('button', { class: 'btn small', onclick: () => {
            if (hs.value === '' || as.value === '') return toast('Enter both scores', 'bad');
            if (confirm(`Final score ${m.home} ${hs.value} - ${as.value} ${m.away}? This locks the pots and cannot be undone.`))
              run(async () => { await api(`/api/admin/matches/${m.id}/finish`, 'POST', { home_score: +hs.value, away_score: +as.value }); load(); }, 'Match settled');
          } }, 'Finish & settle')));
          acts.push(h('button', { class: 'btn small danger', onclick: () => { if (confirm('Cancel this match and refund every stake?')) run(async () => { await api(`/api/admin/matches/${m.id}/cancel`, 'POST'); load(); }, 'Cancelled and refunded'); } }, 'Cancel'));
        }
        if (m.game && ['pending', 'interrupted'].includes(m.game.status))
          acts.push(h('button', { class: 'btn small', onclick: () => run(async () => { await api(`/api/admin/matches/${m.id}/trivia`, 'POST', { action: m.game.status === 'pending' ? 'start_now' : 'resume' }); load(); }, 'Trivia started') }, m.game.status === 'pending' ? 'Start trivia now' : 'Resume trivia'));
        return [`${m.home} v ${m.away}`, fmtDate(m.kickoff_at), statusChip(m.status),
          m.status === 'finished' ? `${m.home_score}–${m.away_score}` : '', `${naira(m.pots.home)} / ${naira(m.pots.away)}`, naira(m.pots.total),
          m.stakers, m.game ? [statusChip(m.game.status), h('div', { class: 'muted' }, m.game.mode)] : '', h('div', { class: 'row' }, acts)];
      });
      show(h('h2', {}, 'Matches & pots'),
        h('div', { class: 'inline' }, h('div', {}, h('label', {}, 'Home team'), k.home), h('div', {}, h('label', {}, 'Away team'), k.away), h('div', {}, h('label', {}, 'Competition'), k.comp),
          h('div', {}, h('label', {}, 'Kickoff'), k.ko), h('div', {}, h('label', {}, 'Staking closes (default kickoff)'), k.close), add),
        h('div', { style: 'height:16px' }),
        table(['Match', 'Kickoff', 'Status', 'Score', 'Home / Away pot', 'Total pot', 'Fans', 'Trivia', 'Actions'], rows),
        h('div', { id: 'detail' }));
    },


    async feed() {
      const d = await api('/api/admin/feed');
      const skew = d.now - Math.floor(Date.now() / 1000);
      const nowS = () => Math.floor(Date.now() / 1000) + skew;
      const ago = (t) => { if (!t) return 'never'; const m = Math.floor((nowS() - t) / 60); return m < 1 ? 'just now' : m + ' min ago'; };
      const fixturesBox = h('div', {});
      const compBar = h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px' });
      const chip = (c) => {
        const b = h('button', { class: 'btn small ' + (VIEWS._comp === c.code ? '' : 'ghost') }, c.name);
        b.addEventListener('click', async () => {
          VIEWS._comp = c.code;
          [...compBar.children].forEach((x, i) => { x.className = 'btn small ' + (d.competitions[i].code === c.code ? '' : 'ghost'); });
          fixturesBox.replaceChildren(h('p', { class: 'muted' }, 'Loading fixtures…'));
          try {
            const { fixtures } = await api('/api/admin/feed/fixtures', 'POST', { competition: c.code });
            fixturesBox.replaceChildren(fixtures.length ? table(['Match', 'Kickoff', ''], fixtures.map((f) => [h('b', {}, `${f.home} v ${f.away}`), fmtDate(f.kickoff_at),
              f.added ? h('span', { class: 'chip ok' }, 'Added') : h('button', { class: 'btn small', onclick: () => run(async () => { await api('/api/admin/feed/add', 'POST', f); load(); }, 'Match added') }, 'Add match')]))
              : h('p', { class: 'muted' }, 'No upcoming fixtures found in the next 2 weeks.'));
          } catch (e) { fixturesBox.replaceChildren(h('p', { style: 'color:#b42323' }, e.message)); }
        });
        return b;
      };
      d.competitions.forEach((c) => compBar.append(chip(c)));

      const syncBtn = h('button', { class: 'btn small ghost', onclick: () => run(async () => { const r = await api('/api/admin/feed/sync', 'POST'); toast(`Checked ${r.checked} matches`); load(); }) }, 'Check scores now');
      const status = h('div', { class: 'row', style: 'margin-bottom:14px' },
        d.demo ? h('span', { class: 'chip gold' }, 'Demo data · add FOOTBALL_DATA_API_KEY for real fixtures')
               : h('span', { class: 'chip ok' }, `Connected to ${d.provider} · synced ${ago(d.last_sync)}`),
        d.demo ? h('span', { class: 'muted' }, 'Last check ' + ago(d.last_sync)) : null, syncBtn,
        d.last_error ? h('span', { style: 'color:#b42323;font-size:13px' }, d.last_error) : null);

      const timers = [];
      const pending = d.pending.map((m) => {
        const cd = h('span', { class: 'muted', style: 'font-size:13px' });
        if (m.settle_at) timers.push(() => { const s = Math.max(0, m.settle_at - nowS()); cd.textContent = `Auto-settles in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; });
        else cd.textContent = m.hold ? 'On hold' : 'Waiting for you';
        const outcome = m.outcome === 'draw' ? `Draw · fans of both clubs play, one winner per club` : `${m.outcome === 'home' ? m.home : m.away} won`;
        return h('div', { class: 'tile', style: 'margin-bottom:10px' },
          h('div', { class: 'row' }, h('b', { style: 'font-size:16px' }, `${m.home} ${m.feed_home} – ${m.feed_away} ${m.away}`), h('span', { class: 'chip' }, 'Full time · from feed'), h('span', { style: 'flex:1' }), cd),
          h('p', { class: 'muted', style: 'margin:6px 0 10px' }, `${outcome} · total pot ${naira(m.pot_kobo)} · ${m.fans} fans staked`),
          h('div', { class: 'row' },
            h('button', { class: 'btn small', onclick: () => { if (confirm(`Settle ${m.home} ${m.feed_home}-${m.feed_away} ${m.away} now? This locks the pots and cannot be undone.`)) run(async () => { await api(`/api/admin/matches/${m.id}/settle-feed`, 'POST'); load(); }, 'Match settled'); } }, 'Settle now'),
            h('button', { class: 'btn small ghost', onclick: () => run(async () => { await api(`/api/admin/matches/${m.id}/hold`, 'POST', { hold: !m.hold }); load(); }) }, m.hold ? 'Release hold' : 'Hold for review')));
      });
      if (timers.length) { const t = () => timers.forEach((f) => f()); t(); VIEWS._t = setInterval(t, 1000); }

      const word = { postponed: 'was postponed', suspended: 'was suspended', cancelled: 'was cancelled', awarded: 'was awarded by a ruling. Check the score before settling by hand' };
      const alerts = d.alerts.map((a) => h('div', { style: 'background:#fdeeee;border:1px solid #e24b4a;border-radius:12px;padding:12px 14px;margin-bottom:10px;color:#501313' },
        h('div', { class: 'row' }, h('div', { style: 'flex:1;min-width:200px' }, h('b', {}, `${a.home} v ${a.away}`), ` ${word[a.status] || a.status} by the feed. Fans have ${naira(a.pot_kobo)} staked.`),
          a.status === 'awarded' ? null : h('button', { class: 'btn small danger', onclick: () => { if (confirm('Cancel this match and refund every stake?')) run(async () => { await api(`/api/admin/matches/${a.id}/cancel`, 'POST'); load(); }, 'Cancelled and refunded'); } }, 'Cancel and refund'))));

      const sw = h('button', { 'aria-label': 'Auto-settle', style: `width:44px;height:26px;border-radius:99px;border:0;cursor:pointer;position:relative;background:${d.auto_settle ? '#d4af37' : '#cfcdc2'}` },
        h('span', { style: `position:absolute;top:3px;${d.auto_settle ? 'right' : 'left'}:3px;width:20px;height:20px;border-radius:50%;background:#fff` }));
      sw.addEventListener('click', () => {
        if (!d.auto_settle && !confirm(`Turn on auto-settle? When the feed reports full time, pots lock automatically after ${d.delay_minutes} minutes. You can still hold any match.`)) return;
        run(async () => { await api('/api/admin/settings', 'PUT', { auto_settle: d.auto_settle ? 0 : 1 }); load(); });
      });

      show(h('h2', {}, 'Fixtures & results feed'), status,
        h('div', { class: 'tile', style: 'background:#fff;margin-bottom:16px' }, h('h3', {}, 'Import upcoming fixtures'), compBar, fixturesBox),
        h('div', { class: 'tile', style: 'background:#fff;border-color:#d4af37;margin-bottom:16px' },
          h('div', { class: 'row' }, h('h3', { style: 'margin:0' }, 'Finished matches waiting to settle'), h('span', { class: 'chip gold' }, String(d.pending.length))),
          h('div', { style: 'height:10px' }), pending.length ? pending : h('p', { class: 'muted' }, 'Nothing waiting. Finished matches appear here when the feed reports full time.')),
        alerts,
        h('div', { class: 'tile', style: 'background:#fff' }, h('div', { class: 'row' },
          h('div', { style: 'flex:1;min-width:220px' }, h('b', {}, 'Auto-settle finished matches'),
            h('div', { class: 'muted', style: 'font-size:13px' }, `Wait ${d.delay_minutes} minutes after full time, then lock the pots. Postponed or awarded matches are never settled automatically. Change the wait in Settings.`)), sw)));
    },

    async wins() {
      const { wins } = await api('/api/admin/wins');
      show(h('h2', {}, 'Prize wins'), table(['Match', 'Round', 'Club', 'Winner', 'Prize', 'Answer time', 'When'],
        wins.map((w) => [`${w.home} v ${w.away}`, w.round_no, w.side === 'home' ? w.home : w.away, [h('b', {}, w.name), h('div', { class: 'muted' }, w.email)], naira(w.amount_kobo), (w.answer_ms / 1000).toFixed(2) + 's', fmtDate(w.resolved_at)])));
    },

    async unclaimed() {
      const d = await api('/api/admin/unclaimed');
      show(h('h2', {}, 'Unclaimed funds'), h('p', { class: 'muted' }, 'Prize money nobody could claim. It stays with the platform. This is the record of which club it came from and why.'),
        h('div', { class: 'grid tiles' }, h('div', { class: 'tile gold' }, h('div', { class: 'muted' }, 'Total unclaimed'), h('div', { class: 'stat' }, naira(d.total_kobo))),
          d.by_club.map((c) => h('div', { class: 'tile' }, h('div', { class: 'muted' }, c.club), h('div', { class: 'stat' }, naira(c.total_kobo)), h('div', { class: 'muted' }, c.n + ' prizes')))),
        h('h3', { style: 'margin-top:20px' }, 'By reason'), table(['Reason', 'Prizes', 'Amount'], d.by_reason.map((r) => [reason[r.reason] || r.reason, r.n, naira(r.total_kobo)])),
        h('h3', { style: 'margin-top:20px' }, 'Every entry'),
        table(['Match', 'Club', 'Round', 'Amount left in pot', 'Why', 'When'], d.rows.map((r) => [`${r.home} v ${r.away}`, h('b', {}, r.club), r.round_no, naira(r.amount_kobo), reason[r.reason] || r.reason, fmtDate(r.created_at)])));
    },

    async users() {
      const q = h('input', { placeholder: 'Search name, email or phone', value: VIEWS._q || '' });
      const go = h('button', { class: 'btn' }, 'Search');
      const out = h('div');
      const search = async () => {
        VIEWS._q = q.value;
        const { users } = await api('/api/admin/users?q=' + encodeURIComponent(q.value));
        out.replaceChildren(table(['Fan', 'Phone', 'Status', 'Balance', 'Staked', 'Won', 'Flags', 'Joined', ''],
          users.map((u) => [[h('b', {}, u.name), h('div', { class: 'muted' }, u.email)], u.phone, statusChip(u.status), naira(u.balance_kobo), naira(u.staked_kobo), naira(u.won_kobo),
            u.open_flags || '', fmtDate(u.created_at), h('button', { class: 'btn small ghost', onclick: () => userDetail(u.id) }, 'Open')])));
      };
      go.addEventListener('click', search);
      q.addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
      show(h('h2', {}, 'Users'), h('div', { class: 'inline' }, h('div', {}, q), go), h('div', { style: 'height:14px' }), out, h('div', { id: 'udetail' }));
      search();
    },

    async withdrawals() {
      const { withdrawals } = await api('/api/admin/withdrawals');
      const act = (ref, action, label, cls) => h('button', { class: 'btn small ' + cls, onclick: () => {
        if (confirm(`${label} this withdrawal?`)) run(async () => { await api('/api/admin/withdrawals/' + ref, 'POST', { action }); load(); }, 'Done'); } }, label);
      show(h('h2', {}, 'Withdrawals'), table(['When', 'Fan', 'Bank', 'Amount', 'Status', ''],
        withdrawals.map((w) => [fmtDate(w.created_at), [h('b', {}, w.name), h('div', { class: 'muted' }, w.email)], `${w.bank_name || ''} ${w.account_number || ''} ${w.account_name || ''}`,
          h('b', {}, naira(w.amount_kobo)), [statusChip(w.status), w.detail ? h('div', { class: 'muted' }, w.detail) : null],
          w.status === 'pending_approval' ? h('div', { class: 'row' }, act(w.reference, 'approve', 'Approve', ''), act(w.reference, 'decline', 'Decline', 'danger'))
            : w.status === 'needs_attention' ? act(w.reference, 'decline', 'Decline & refund', 'danger') : ''])));
    },

    async questions() {
      const { questions } = await api('/api/admin/questions');
      const t = h('input'), opts = [0, 1, 2, 3].map((i) => h('input', { placeholder: 'Option ' + 'ABCD'[i] }));
      const correct = h('select', {}, [0, 1, 2, 3].map((i) => h('option', { value: i }, 'Correct: ' + 'ABCD'[i])));
      const add = h('button', { class: 'btn' }, 'Add question');
      add.addEventListener('click', () => run(async () => {
        await api('/api/admin/questions', 'POST', { text: t.value, options: opts.map((o) => o.value).filter(Boolean), correct_index: +correct.value });
        load();
      }, 'Question added'));
      const bulk = h('textarea', { placeholder: 'One per line:\nWho won the 2010 World Cup? | Spain | Brazil | Germany | Italy | 1\n(the last number is which option is correct: 1-4)' });
      const imp = h('button', { class: 'btn ghost' }, 'Import list');
      imp.addEventListener('click', () => run(async () => {
        const items = bulk.value.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
          const p = l.split('|').map((x) => x.trim());
          return { text: p[0], options: p.slice(1, -1), correct_index: Number(p[p.length - 1]) - 1 };
        });
        const r = await api('/api/admin/questions', 'POST', { items });
        toast(r.added + ' questions imported'); load();
      }));
      show(h('h2', {}, `Trivia questions (${questions.filter((q) => q.active).length} active)`),
        h('div', { class: 'card', style: 'border-color:#e3e1d8;background:#fff' }, h('label', {}, 'Question'), t, h('div', { class: 'inline' }, opts.map((o) => h('div', {}, o))), h('div', { style: 'height:8px' }), h('div', { class: 'inline' }, h('div', {}, correct), add)),
        h('details', {}, h('summary', { style: 'cursor:pointer' }, 'Bulk import'), bulk, imp),
        table(['Question', 'Options', 'Used', ''], questions.map((q) => [h('span', { class: q.active ? '' : 'muted' }, q.text),
          q.options.map((o, i) => h('div', { class: i === q.correct_index ? 'gold' : 'muted', style: i === q.correct_index ? 'font-weight:700;color:#177a43' : '' }, (i === q.correct_index ? '✓ ' : '· ') + o)),
          q.times_used, q.active ? h('button', { class: 'btn small danger', onclick: () => run(async () => { await api('/api/admin/questions/' + q.id, 'DELETE'); load(); }) }, 'Retire') : h('span', { class: 'muted' }, 'retired')])));
    },

    async content() {
      const c = await api('/api/admin/content');
      const f = {};
      const labels = { site_name: 'Site name', tagline: 'Tagline', banner: 'Top banner (leave empty to hide)', hero_title: 'Homepage headline', hero_text: 'Homepage intro text',
        hero_image_url: 'Homepage image URL (optional, https)', how_title: '"How it works" title', how_text: '"How it works" text (one step per line)', rules_title: 'Rules title', rules_text: 'Rules text (one line per rule)',
        faq_title: 'FAQ title', faq_text: 'FAQ text', terms_title: 'Terms title', terms_text: 'Terms & responsible play text', footer_text: 'Footer text' };
      const long = ['hero_text', 'how_text', 'rules_text', 'faq_text', 'terms_text'];
      const form = Object.keys(labels).map((k) => [h('label', {}, labels[k]), (f[k] = long.includes(k) ? h('textarea', { value: c[k], style: 'min-height:130px' }) : h('input', { value: c[k] }))]);
      const save = h('button', { class: 'btn', style: 'margin-top:14px' }, 'Save changes');
      save.addEventListener('click', () => run(async () => { const d = {}; Object.keys(f).forEach((k) => { d[k] = f[k].value; }); await api('/api/admin/content', 'PUT', d); }, 'Saved. The site now shows your changes.'));
      show(h('h2', {}, 'Website content'), h('p', { class: 'muted' }, 'Everything fans read on the site. Plain text only.'), form, save);
    },

    async settings() {
      const s = await api('/api/admin/settings');
      const SPEC = [
        ['win_round_bp', 'Straight win: prize per round (% of total pot, 1 winner)', 'pct'], ['draw_round_bp', 'Draw: prize per round (% of total pot, split between 2 winners)', 'pct'],
        ['draw_one_sided_full_prize', 'Draw with only one club staked: winner gets the full round prize?', 'bool'],
        ['max_retakes', 'Retakes per round before the prize goes to the platform', 'num'], ['countdown_s', 'Get-ready countdown (seconds)', 'num'], ['answer_window_s', 'Time to answer (seconds)', 'num'],
        ['pause_s', 'Pause between rounds (seconds)', 'num'], ['trivia_delay_minutes', 'Minutes between final whistle and trivia', 'num'],
        ['grace_ms', 'Fairness: wait after first correct answer (ms)', 'num'], ['latency_cap_ms', 'Fairness: max network delay compensated (ms)', 'num'],
        ['min_human_ms', 'Anti-cheat: answers faster than this are disqualified (ms)', 'num'],
        ['min_stake_kobo', 'Minimum stake (₦)', 'naira'], ['max_stake_kobo', 'Maximum stake per fan per match (₦)', 'naira'], ['min_withdraw_kobo', 'Minimum withdrawal (₦)', 'naira'],
        ['withdrawals_require_approval', 'Admin must approve every withdrawal?', 'bool'],
        ['auto_settle', 'Results feed: settle finished matches automatically?', 'bool'], ['auto_settle_delay_minutes', 'Results feed: minutes to wait after full time before settling', 'num']];
      const f = {};
      const toUi = (v, u) => (u === 'pct' ? v / 100 : u === 'naira' ? v / 100 : v);
      const fromUi = (v, u) => Math.round(u === 'pct' ? v * 100 : u === 'naira' ? v * 100 : v);
      const form = SPEC.map(([k, label, u]) => [h('label', {}, label),
        (f[k] = u === 'bool' ? h('select', {}, h('option', { value: 1, selected: !!s[k] }, 'Yes'), h('option', { value: 0, selected: !s[k] }, 'No')) : h('input', { type: 'number', step: u === 'num' ? '1' : '0.01', value: toUi(s[k], u) }))]);
      const preview = h('p', { class: 'muted' });
      const upd = () => { const w = Number(f.win_round_bp.value), d = Number(f.draw_round_bp.value); preview.textContent = `Platform keeps ${(100 - 3 * w).toFixed(1)}% of the pot on a straight win and ${(100 - 3 * d).toFixed(1)}% on a draw (plus any unclaimed prizes).`; };
      f.win_round_bp.addEventListener('input', upd); f.draw_round_bp.addEventListener('input', upd); upd();
      const save = h('button', { class: 'btn', style: 'margin-top:14px' }, 'Save settings');
      save.addEventListener('click', () => run(async () => {
        const d = {}; SPEC.forEach(([k, , u]) => { d[k] = fromUi(Number(f[k].value), u); });
        await api('/api/admin/settings', 'PUT', d);
      }, 'Settings saved. They apply from the next match or round.'));
      show(h('h2', {}, 'Settings'), preview, form, save);
    },

    async flags() {
      const { flags } = await api('/api/admin/flags');
      const kind = { too_fast: 'Answered faster than a human can read', shared_device: 'Several accounts on one device' };
      show(h('h2', {}, 'Anti-cheat flags'), h('p', { class: 'muted' }, 'Withdrawals by a flagged fan always need your manual approval until the flag is resolved.'),
        table(['When', 'Fan', 'Flag', 'Detail', 'Match', ''], flags.map((f) => [fmtDate(f.created_at), f.name ? [h('b', {}, f.name), h('div', { class: 'muted' }, f.email)] : '', kind[f.kind] || f.kind, f.detail, f.match_id || '',
          f.resolved ? h('span', { class: 'muted' }, 'resolved') : h('button', { class: 'btn small ghost', onclick: () => run(async () => { await api(`/api/admin/flags/${f.id}/resolve`, 'POST'); load(); }) }, 'Mark resolved')])));
    },
  };

  // ---------------------------------------------------------- detail panels
  async function detail(id) {
    const d = await api('/api/admin/matches/' + id), m = d.match, el = document.getElementById('detail');
    el.replaceChildren(h('div', { class: 'tile', style: 'margin-top:20px' },
      h('h3', {}, `${m.home} v ${m.away}`),
      h('p', {}, `Home pot ${naira(m.pots.home)} (${m.pots.home_fans} fans) · Away pot ${naira(m.pots.away)} (${m.pots.away_fans} fans) · Total ${naira(m.pots.total)}`),
      h('h3', {}, 'Prize slots'), table(['Round', 'Club', 'Prize', 'Status', 'Winner', 'Time', 'Note'],
        d.slots.map((s) => [s.round_no, club(m, s.side), naira(s.amount_kobo), statusChip(s.status), s.winner_name ? [s.winner_name, h('div', { class: 'muted' }, s.winner_email)] : '', s.answer_ms ? (s.answer_ms / 1000).toFixed(2) + 's' : '', reason[s.reason] || s.reason || ''])),
      h('h3', { style: 'margin-top:14px' }, 'Rounds played'), h('p', { class: 'muted' }, d.rounds.map((r) => `R${r.round_no}${r.attempt ? ' retake ' + r.attempt : ''} (${r.sides})`).join(' · ') || 'none yet'),
      h('h3', { style: 'margin-top:14px' }, 'Stakers'), table(['Fan', 'Backed', 'Staked'], d.stakers.map((s) => [[s.name, h('div', { class: 'muted' }, s.email)], club(m, s.side), naira(s.amount_kobo)]))));
    el.scrollIntoView({ behavior: 'smooth' });
  }

  async function userDetail(id) {
    const d = await api('/api/admin/users/' + id), p = d.profile, el = document.getElementById('udetail');
    const toggle = h('button', { class: 'btn small ' + (p.status === 'active' ? 'danger' : ''), onclick: () => {
      if (confirm(`${p.status === 'active' ? 'Suspend' : 'Reactivate'} ${p.name}?`)) run(async () => { await api(`/api/admin/users/${id}/status`, 'POST', { status: p.status === 'active' ? 'suspended' : 'active' }); userDetail(id); }, 'Updated'); } },
      p.status === 'active' ? 'Suspend account' : 'Reactivate account');
    el.replaceChildren(h('div', { class: 'tile', style: 'margin-top:20px' },
      h('div', { class: 'row', style: 'justify-content:space-between' }, h('h3', {}, p.name), toggle),
      h('p', {}, `${p.email} · ${p.phone} · joined ${fmtDate(p.created_at)} · balance `, h('b', {}, naira(p.balance_kobo))),
      h('p', { class: 'muted' }, `Bank: ${p.bank_name || 'not added'} ${p.account_number || ''} ${p.account_name || ''} · Signup IP ${p.signup_ip || '?'} · Device ${p.device_id ? p.device_id.slice(0, 8) + '…' : '?'}`),
      h('h3', {}, 'Stakes'), table(['Match', 'Backed', 'Amount', 'When'], d.stakes.map((s) => [`${s.home} v ${s.away}`, club(s, s.side), naira(s.amount_kobo), fmtDate(s.created_at)])),
      h('h3', { style: 'margin-top:14px' }, 'Wins'), table(['Match', 'Round', 'Prize', 'Time'], d.wins.map((w) => [`${w.home} v ${w.away}`, w.round_no, naira(w.amount_kobo), (w.answer_ms / 1000).toFixed(2) + 's'])),
      h('h3', { style: 'margin-top:14px' }, 'Payments'), table(['Type', 'Amount', 'Status', 'When'], d.payments.map((x) => [x.kind, naira(x.amount_kobo), statusChip(x.status), fmtDate(x.created_at)])),
      h('h3', { style: 'margin-top:14px' }, 'Flags'), table(['Flag', 'Detail', 'Resolved'], d.flags.map((f) => [f.kind, f.detail, f.resolved ? 'yes' : 'no']))));
    el.scrollIntoView({ behavior: 'smooth' });
  }

  // ---------------------------------------------------------- login gate
  function loginForm(msg) {
    const e = h('input', { type: 'email' }), p = h('input', { type: 'password' }), b = h('button', { class: 'btn', style: 'width:100%;margin-top:12px' }, 'Log in');
    const err = h('p', { style: 'color:#b42323' }, msg || '');
    b.addEventListener('click', async () => { try { await api('/api/login', 'POST', { email: e.value, password: p.value }); start(); } catch (x) { err.textContent = x.message; } });
    root.replaceChildren(h('div', { class: 'wrap', style: 'max-width:420px;padding-top:12vh' }, h('div', { class: 'panel' }, h('h2', {}, 'Admin login'), h('label', {}, 'Email'), e, h('label', {}, 'Password'), p, err, b)));
  }

  async function start() {
    const { user } = await api('/api/me');
    if (!user) return loginForm();
    if (user.role !== 'admin') return loginForm('This account is not an admin.');
    shell(user);
  }
  start();
})();
