import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isLoopback, makeKeyLender } from '../proxy-auth.ts';

test('only this machine counts as loopback', () => {
  for (const ok of ['127.0.0.1', '::1', '::ffff:127.0.0.1']) assert.equal(isLoopback(ok), true);
  for (const bad of ['192.168.1.20', '10.0.0.5', '::ffff:10.0.0.5', '203.0.113.9', '', undefined]) {
    assert.equal(isLoopback(bad), false);
  }
});

test('the key is lent to loopback callers and withheld from everyone else', () => {
  const lend = makeKeyLender('secret-key');
  const headers = (address) => {
    const set = {};
    lend({ setHeader: (k, v) => { set[k] = v; } }, { socket: { remoteAddress: address } });
    return set;
  };
  assert.deepEqual(headers('127.0.0.1'), { Authorization: 'Bearer secret-key' });
  assert.deepEqual(headers('192.168.1.20'), {});
  assert.deepEqual(headers(undefined), {});
  const noKey = makeKeyLender('');
  const set = {};
  noKey({ setHeader: (k, v) => { set[k] = v; } }, { socket: { remoteAddress: '127.0.0.1' } });
  assert.deepEqual(set, {});
});
