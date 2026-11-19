// HEALTH_URLS is a whitespace-separated list of HTTPS /health endpoints.
export default {
    async scheduled(_event, env) {
        const urls = (env.HEALTH_URLS || '').trim().split(/\s+/).filter(Boolean);
        if (!urls.length) throw new Error('health endpoints missing');
        let failed = false;
        for (const [i, raw] of urls.entries()) {
            try {
                const url = new URL(raw);
                if (url.protocol !== 'https:' || url.username || url.password ||
                    url.pathname !== '/health' || url.search || url.hash) throw new Error();
                const res = await fetch(url, {redirect: 'error', signal: AbortSignal.timeout(15000)});
                if (res.status !== 200 || await res.text() !== 'ok\n') throw new Error();
                console.log('health', i + 1, 'ok');
            } catch {
                console.log('health', i + 1, 'failed');
                failed = true;
            }
        }
        if (failed) throw new Error('health check failed');
    }
};
