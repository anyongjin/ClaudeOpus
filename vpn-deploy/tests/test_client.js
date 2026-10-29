"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../client-script.js"), "utf8");
const context = {};
vm.createContext(context);
vm.runInContext(source, context);
const plain = value => JSON.parse(JSON.stringify(value));
const generate = config => plain(context.main(plain(config)));
const base = {
  mode: "rule",
  proxies: [{name: "Airport", type: "socks5", server: "192.0.2.1", port: 1080}],
  "proxy-groups": [
    {name: "DEFAULT", type: "select", proxies: ["Airport", "DIRECT"]},
    {name: "Media", type: "select", proxies: ["DEFAULT", "DIRECT"]}
  ],
  rules: ["DOMAIN-SUFFIX,example.cn,DIRECT", "MATCH,DEFAULT"]
};

const result = generate(base);
assert.deepEqual(result["proxy-groups"].slice(0, 2), base["proxy-groups"]);
assert.deepEqual(result.proxies[0], base.proxies[0]);
assert.deepEqual(result["proxy-groups"].at(-1), {
  name: "AI-US-VIA-CN", type: "select", proxies: ["CN-GATEWAY-HY2"]
});
assert.equal(result.proxies.at(-1).server, "<CN_HOST>");
assert.equal(result.proxies.at(-1).port, "<CN_PORT>");
assert.equal(result.proxies.at(-1).password, "<CN_PASSWORD>");
assert.equal(result.proxies.at(-1).sni, "<CN_SNI>");
assert.equal(result.proxies.at(-1).fingerprint, "<CN_CERT_SHA256>");
assert.equal(result.rules.at(-1), "MATCH,DEFAULT");
assert.deepEqual(generate(result), result);
assert.equal(result.proxies.filter(p => p.name === "CN-GATEWAY-HY2").length, 1);
assert.equal(result["proxy-groups"].filter(g => g.name === "AI-US-VIA-CN").length, 1);

const migrated = generate({
  ...base,
  "proxy-groups": base["proxy-groups"].concat([
    {name: "CLAUDE-LOCKED", type: "select", proxies: ["Airport"]},
    {name: "AI-US-VIA-CN", type: "select", proxies: ["Airport"]}
  ]),
  rules: ["DOMAIN-SUFFIX,claude.ai,CLAUDE-LOCKED", ...result.rules]
});
assert.deepEqual(migrated, result);
assert.ok(!migrated["proxy-groups"].some(g => g.name === "CLAUDE-LOCKED"));
assert.ok(!migrated.rules.some(r => r.includes("CLAUDE-LOCKED")));
assert.equal(result.rules.filter(r =>
  r.startsWith("DOMAIN-SUFFIX,") && r.endsWith(",AI-US-VIA-CN")).length, 56);

for (const domain of [
  "claude.ai", "claude.com", "anthropic.com", "chatgpt.com", "openai.com",
  "gemini.google.com", "aistudio.google.com", "notebooklm.google.com",
  "aiplatform.googleapis.com", "discoveryengine.googleapis.com", "grok.com",
  "copilot.microsoft.com", "huggingface.co", "antigravity.google",
  "alkalicore-pa.clients6.google.com"
]) {
  assert.ok(result.rules.includes(`DOMAIN-SUFFIX,${domain},AI-US-VIA-CN`), domain);
}
const aiIndex = result.rules.indexOf("DOMAIN-SUFFIX,gemini.google.com,AI-US-VIA-CN");
const normalIndex = result.rules.indexOf("DOMAIN-SUFFIX,google.com,DEFAULT");
assert.ok(aiIndex >= 0 && aiIndex < normalIndex);
assert.ok(result.rules.includes("GEOSITE,google,DEFAULT"));
assert.ok(result.rules.includes("DOMAIN-SUFFIX,youtube.com,DEFAULT"));
assert.ok(!result.rules.includes("DOMAIN-SUFFIX,google.com,AI-US-VIA-CN"));
assert.ok(!result.rules.includes("DOMAIN-SUFFIX,challenges.cloudflare.com,AI-US-VIA-CN"));
assert.ok(result.rules.includes("DOMAIN-SUFFIX,example.cn,DIRECT"));
const regionalRule = result.rules.find(r => r.startsWith("DOMAIN-REGEX,"));
const regional = new RegExp(regionalRule.split(",")[1]);
assert.ok(regional.test("us-central1-aiplatform.googleapis.com"));
assert.ok(regional.test("us-discoveryengine.googleapis.com"));
assert.ok(!regional.test("us-aiplatform.googleapis.com.evil.example"));
assert.ok(!regional.test("www.googleapis.com"));

assert.throws(() => generate({...base, rules: []}), /MATCH/);
assert.throws(() => generate({...base, rules: ["MATCH,AI-US-VIA-CN"]}), /default/i);
assert.throws(() => generate({...base, rules: ["MATCH,CN-GATEWAY-HY2"]}), /default/i);
assert.throws(() => generate({
  ...base,
  "proxy-groups": base["proxy-groups"].concat([{name: "CN-GATEWAY-HY2", type: "select", proxies: ["Airport"]}])
}), /collision/i);
assert.throws(() => generate({
  ...base,
  proxies: base.proxies.concat([{name: "AI-US-VIA-CN", type: "socks5", server: "192.0.2.2", port: 1080}])
}), /collision/i);

console.log("PASS: client template, migration, idempotence, AI/Google boundaries and conflict checks");
