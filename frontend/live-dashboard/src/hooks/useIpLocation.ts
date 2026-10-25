import { useEffect, useState } from 'react';

/** Free IP geolocation (https, CORS-enabled). It sees this browser's public IP. */
export const IP_LOOKUP_URL = 'https://ipwho.is/';

type IpWho = { success?: boolean; city?: string; region_code?: string; region?: string; country_code?: string };

/** "Irvine, CA" from an ipwho.is answer; undefined if it has no place. */
export function ipPlace(body: IpWho | null | undefined): string | undefined {
  if (!body || body.success === false) return undefined;
  const area = body.region_code || body.region || body.country_code;
  const parts = [body.city, area].filter(Boolean);
  return parts.length ? parts.join(', ') : undefined;
}

/**
 * Rough location from this browser's public IP, looked up once per page
 * load. Shown on the dashboard until a GPS fix arrives.
 */
export function useIpLocation(fetcher: typeof fetch = fetch, url = IP_LOOKUP_URL): string | undefined {
  const [place, setPlace] = useState<string>();
  useEffect(() => {
    let stop = false;
    fetcher(url)
      .then(res => (res.ok ? res.json() : null))
      .then(body => {
        if (!stop) setPlace(ipPlace(body));
      })
      .catch(() => {
        /* offline or blocked: the heading falls back to plain text */
      });
    return () => {
      stop = true;
    };
  }, [fetcher, url]);
  return place;
}
