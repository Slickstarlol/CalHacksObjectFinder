/** Some belongings couldn't be deleted; `deleted` lists the ones that were. */
export class DeleteError extends Error {
  constructor(
    message: string,
    readonly deleted: string[]
  ) {
    super(message);
    this.name = 'DeleteError';
  }
}

/** The server's answer when the item itself isn't there (search.py). */
const NO_SUCH_ITEM = /^No registered belonging/;
/** A 404 without that detail means the route is missing: the server predates it. */
export const STALE_SERVER =
  'The RecallAI server is running an older version. Restart it (launcher: Stop, then Start the server step) and try again.';

async function body(res: Response): Promise<{ name?: unknown; detail?: unknown }> {
  try {
    return (await res.json()) as { name?: unknown; detail?: unknown };
  } catch {
    return {}; // non-JSON error
  }
}

const gone = (b: { detail?: unknown }) => typeof b.detail === 'string' && NO_SUCH_ITEM.test(b.detail);

/**
 * Rename a belonging (PUT /api/belongings/{id}). Resolves with the saved
 * name (whitespace tidied by the server); rejects with a readable message.
 */
export async function renameBelonging(id: string, name: string, apiBase = '', fetchImpl: typeof fetch = fetch): Promise<string> {
  let res: Response;
  try {
    res = await fetchImpl(`${apiBase.replace(/\/+$/, '')}/api/belongings/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
  } catch {
    throw new Error('Can’t reach the RecallAI server to save the name.');
  }
  const b = await body(res);
  if (res.ok) return typeof b.name === 'string' ? b.name : name.trim();
  if (res.status === 404 || res.status === 405) {
    throw new Error(gone(b) ? 'That belonging no longer exists.' : STALE_SERVER);
  }
  throw new Error(typeof b.detail === 'string' ? b.detail : 'Couldn’t save the name.');
}

/**
 * Delete belongings (DELETE /api/belongings/{id}): their reference photos
 * and history. Already-gone ones count as deleted. Resolves with the
 * deleted ids, or rejects with a DeleteError naming what failed.
 */
export async function deleteBelongings(
  ids: readonly string[],
  apiBase = '',
  fetchImpl: typeof fetch = fetch
): Promise<string[]> {
  const base = apiBase.replace(/\/+$/, '');
  const deleted: string[] = [];
  const failed: string[] = [];
  for (const id of ids) {
    try {
      const res = await fetchImpl(`${base}/api/belongings/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (res.ok) deleted.push(id);
      else if (res.status === 404 || res.status === 405) {
        if (gone(await body(res))) deleted.push(id);
        else throw new DeleteError(STALE_SERVER, deleted);
      } else failed.push(id);
    } catch (err) {
      if (err instanceof DeleteError) throw err;
      throw new DeleteError('Can’t reach the RecallAI server to delete.', deleted);
    }
  }
  if (failed.length) {
    throw new DeleteError(
      `Couldn’t delete ${failed.length === 1 ? 'one belonging' : `${failed.length} belongings`}. Try again.`,
      deleted
    );
  }
  return deleted;
}
