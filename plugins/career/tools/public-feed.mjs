// Public discovery over HTTPS, with no credentials in the address; career names no websites.
export function publicURL(url) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) {
    throw new Error('A public company HTTPS feed is required');
  }
  return parsed;
}
export async function publicFetch(url, init = {}, redirects = 0) {
  publicURL(url);
  const response = await fetch(url, {...init, redirect: 'manual'});
  if ([301, 302, 303, 307, 308].includes(response.status)) {
    if (redirects >= 5) throw new Error('Too many feed redirects');
    const next = new URL(response.headers.get('location'), url).href;
    return publicFetch(next, response.status === 303 ? {...init, method: 'GET', body: undefined} : init, redirects + 1);
  }
  return response;
}
