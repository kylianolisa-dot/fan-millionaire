const ref = new URLSearchParams(location.search).get('reference');
document.getElementById('pay').addEventListener('click', async () => {
  try {
    await api('/api/mock/pay', 'POST', { reference: ref });
    location.href = '/pay-return.html?reference=' + encodeURIComponent(ref);
  } catch (e) { toast(e.message, 'bad'); }
});
