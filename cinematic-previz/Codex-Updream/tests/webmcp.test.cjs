'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const stale = 'WebMCP tool registration is stale. Call fetchTools() again.';

async function readTool() {
  return (await import('../plugin/skills/updream/scripts/webmcp-read.mjs')).readUpdreamTool;
}

function fakeWebMcp(catalogs, results) {
  const calls = [];
  let fetches = 0;
  return {
    calls,
    get fetches() { return fetches; },
    async fetchTools() {
      const catalog = catalogs[fetches++];
      return {
        description: () => 'Current tools:\n' + JSON.stringify(catalog) + '\nCall tools.call(name, input).',
        async call(name, input) {
          calls.push({ name, input });
          const result = results[calls.length - 1];
          if (result instanceof Error) throw result;
          return result;
        },
      };
    },
  };
}

test('a stale read fetches a new catalog and uses its newly registered name', async () => {
  const read = await readTool();
  const input = { limit: 50 };
  const result = { ok: true, totalNodes: 69 };
  const web = fakeWebMcp(
    [[{ name: 'canvas_getSnapshot__old' }], [{ name: 'canvas_getSnapshot__new' }]],
    [new Error(stale), result],
  );
  assert.deepEqual(await read(web, 'canvas_getSnapshot', input), result);
  assert.equal(web.fetches, 2);
  assert.deepEqual(web.calls, [
    { name: 'canvas_getSnapshot__old', input },
    { name: 'canvas_getSnapshot__new', input },
  ]);
});

test('two stale registrations surface the failure without a third call', async () => {
  const read = await readTool();
  const tools = [{ name: 'project_getCurrent__id' }];
  const web = fakeWebMcp([tools, tools], [new Error(stale), new Error(stale)]);
  await assert.rejects(read(web, 'project_getCurrent'), /registration.*stale/i);
  assert.equal(web.fetches, 2);
  assert.equal(web.calls.length, 2);
});

test('timeouts propagate without repeating the call', async () => {
  const read = await readTool();
  const failure = new Error('Read timed out');
  const web = fakeWebMcp([[{ name: 'project_list__id' }]], [failure]);
  await assert.rejects(read(web, 'project_list'), error => error === failure);
  assert.equal(web.fetches, 1);
  assert.equal(web.calls.length, 1);
});

test('write operations never reach the catalog or site', async () => {
  const read = await readTool();
  const web = fakeWebMcp([], []);
  await assert.rejects(read(web, 'project_open', { projectId: '123' }), /read-only/i);
  assert.equal(web.fetches, 0);
  assert.deepEqual(web.calls, []);
});

test('missing or ambiguous tools cause no site call', async () => {
  const read = await readTool();
  for (const [catalog, message] of [
    [[{ name: 'canvas_getSnapshotExtra__id' }], /not advertised/i],
    [[{ name: 'canvas_getSnapshot__a' }, { name: 'canvas_getSnapshot__b' }], /ambiguous/i],
  ]) {
    const web = fakeWebMcp([catalog], []);
    await assert.rejects(read(web, 'canvas_getSnapshot'), message);
    assert.deepEqual(web.calls, []);
  }
});
