const form = document.getElementById('font-check');
const consent = document.getElementById('consent');
const button = document.getElementById('go');
const status = document.getElementById('status');
const label = document.getElementById('label');
let pending = null;
let busy = false;
let saved = false;

consent.addEventListener('change', () => {
  button.disabled = busy || saved || !consent.checked;
  status.textContent = consent.checked ? 'Ready when you are.' : 'Confirm your consent to enable the button.';
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || saved || !consent.checked) return;
  if (!isSecureContext) {
    status.textContent = 'Open this page over HTTPS to run the font check.';
    return;
  }
  busy = true;
  button.disabled = true;
  consent.disabled = true;
  label.disabled = true;
  form.setAttribute('aria-busy', 'true');
  try {
    if (!pending) {
      status.textContent = 'Measuring the sample text…';
      // Load and invoke the measurement module only after the explicit action.
      const {collectFontContextSupplement} = await import('/diagnostics/font-context/probe.js');
      const result = await collectFontContextSupplement();
      pending = {consent: {accepted: true, version: 'font-context-v1'}, label: label.value.trim(), result};
      document.getElementById('out').textContent = JSON.stringify(pending, null, 2);
      document.getElementById('details').hidden = false;
    }
    status.textContent = 'Sending the diagnostic…';
    const response = await fetch('/diagnostics/font-context/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Diagnostic-Token': document.querySelector('meta[name=diagnostic-token]').content},
      body: JSON.stringify(pending),
      signal: AbortSignal.timeout(20000)
    });
    const reply = await response.json();
    if (!response.ok || !reply.ok) throw new Error(reply.error || 'The server did not accept the diagnostic.');
    saved = true;
    button.textContent = 'Font check sent';
    status.textContent = 'Saved as ' + reply.id + '. Thank you. You can close this tab.';
    document.documentElement.dataset.diagnosticDone = '1';
  } catch (error) {
    status.textContent = 'The check was not confirmed saved. ' + (error.message || 'Please try again.');
    button.textContent = pending ? 'Send the same diagnostic again' : 'Run and send font check';
  } finally {
    busy = false;
    button.disabled = saved || !consent.checked;
    consent.disabled = saved || !!pending;
    label.disabled = saved || !!pending;
    form.setAttribute('aria-busy', 'false');
  }
});
