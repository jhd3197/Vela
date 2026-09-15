// A trusted, script-free wrapper constrains every navigation of the service
// frame, including HTTP redirects. No Vela SDK or bearer enters either frame.
export function connectedAddress(value, hubOrigin) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error();
    if (url.hostname === new URL(hubOrigin).hostname)
      throw new Error(
        'Use a different hostname from Vela. Cookies are shared across ports on the same hostname.',
      );
    return url;
  } catch (error) {
    throw new Error(
      error.message.startsWith('Use a different')
        ? error.message
        : 'Use an HTTPS service address separate from Vela.',
      { cause: error },
    );
  }
}

function escapeAttribute(value) {
  return value.replace(
    /[&<>"']/g,
    (char) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[char],
  );
}

export function connectedDocument(value, name, hubOrigin) {
  const url = connectedAddress(value, hubOrigin);
  const policy = `default-src 'none'; script-src 'none'; frame-src ${url.origin}; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`;
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${escapeAttribute(policy)}">
<meta name="referrer" content="no-referrer">
<style>html,body{height:100%;margin:0;overflow:hidden}iframe{display:block;width:100%;height:100%;border:0}</style>
</head><body><iframe src="${escapeAttribute(url.href)}" title="${escapeAttribute(name)}"
sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups"
referrerpolicy="no-referrer"></iframe></body></html>`;
}
