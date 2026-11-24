// Paystack sends the user back here with ?reference=... . We ask the server to verify it with Paystack.
(async () => {
  const ref = new URLSearchParams(location.search).get('reference') || new URLSearchParams(location.search).get('trxref');
  const t = document.getElementById('t'), m = document.getElementById('m');
  if (!ref) { t.textContent = 'No payment found'; m.textContent = ''; return; }
  for (let i = 0; i < 6; i++) {
    try {
      const r = await api('/api/wallet/verify', 'POST', { reference: ref });
      if (r.status === 'success') { t.textContent = 'Payment received ✓'; m.textContent = 'Your wallet has been credited.'; return; }
      if (r.status === 'failed' || r.status === 'abandoned' || r.status === 'mismatch') {
        t.textContent = 'Payment not completed'; m.textContent = 'Nothing was charged to your wallet. Try again.'; return;
      }
    } catch (e) {
      if (e.status === 401) { t.textContent = 'Please log in'; m.textContent = 'Log in, then open your wallet to see your payment.'; return; }
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  t.textContent = 'Still confirming…'; m.textContent = 'This can take a minute. Your wallet will update automatically once Paystack confirms.';
})();
