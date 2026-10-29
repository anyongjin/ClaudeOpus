// Render private gateway parameters before loading this global enhancement script.
function main(config) {
  const gateway = {
    name: "CN-GATEWAY-HY2", type: "hysteria2",
    server: "<CN_HOST>", port: "<CN_PORT>",
    password: "<CN_PASSWORD>", sni: "<CN_SNI>",
    "skip-cert-verify": true, fingerprint: "<CN_CERT_SHA256>",
    alpn: ["h3"]
  };
  const group = "AI-US-VIA-CN";
  const replaced = new Set([group, "CLAUDE-LOCKED"]);
  const rules = config.rules || [];
  const match = rules.find(r => /^MATCH,/i.test(r));
  if (!match) throw new Error("Subscription has no MATCH rule");
  const normal = match.split(",")[1];
  if (replaced.has(normal) || normal === gateway.name) {
    throw new Error("Invalid default proxy group");
  }
  if ((config.proxies || []).some(p => replaced.has(p.name)) ||
      (config["proxy-groups"] || []).some(g => g.name === gateway.name)) {
    throw new Error("AI split proxy/group name collision");
  }
  config.proxies = (config.proxies || [])
    .filter(p => p.name !== gateway.name).concat([gateway]);
  config["proxy-groups"] = (config["proxy-groups"] || [])
    .filter(g => !replaced.has(g.name))
    .concat([{name: group, type: "select", proxies: [gateway.name]}]);
  const ai = [
    "claude.ai", "claude.com", "anthropic.com",
    "claudeusercontent.com", "claudeusercontent.net",
    "openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com",
    "chat.com", "sora.com", "x.ai", "grok.com", "perplexity.ai", "pplx.ai",
    "huggingface.co", "hf.co", "copilot.microsoft.com",
    "copilot.cloud.microsoft", "githubcopilot.com", "copilot.github.com",
    "gemini.google.com", "gemini.google", "aistudio.google.com",
    "makersuite.google.com", "bard.google.com", "ai.google.dev", "ai.google",
    "generativelanguage.googleapis.com", "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com", "vertexai.google",
    "notebooklm.google.com", "notebooklm.google", "notebooklm.cloud.google.com",
    "notebooklm.googleapis.com", "notebooklm-pa.googleapis.com",
    "labs.google", "jules.google", "jules.google.com", "stitch.withgoogle.com",
    "antigravity.google", "antigravity.google.com", "flow.google", "whisk.google",
    "opal.google", "deepmind.google", "deepmind.com", "generativeai.google",
    "cloudaicompanion.googleapis.com", "cloudcode-pa.googleapis.com",
    "daily-cloudcode-pa.googleapis.com", "antigravity-pa.googleapis.com",
    "geminicloudassist.googleapis.com", "alkalimakersuite-pa.clients6.google.com",
    "alkalicore-pa.clients6.google.com"
  ];
  const google = ["google.com", "googleapis.com", "gstatic.com",
    "googleusercontent.com", "google.dev", "google", "youtube.com",
    "youtu.be", "ytimg.com", "googlevideo.com", "ggpht.com", "withgoogle.com"];
  const priority = ai.map(d => `DOMAIN-SUFFIX,${d},${group}`)
    .concat([`DOMAIN-REGEX,^([a-z0-9-]+-)?(aiplatform|discoveryengine)\\.googleapis\\.com$,${group}`])
    .concat(google.map(d => `DOMAIN-SUFFIX,${d},${normal}`))
    .concat([`GEOSITE,google,${normal}`]);
  const owned = new Set(priority);
  config.rules = priority.concat(rules.filter(r =>
    !owned.has(r) && !r.split(",").some(x => replaced.has(x))));
  config.mode = "rule";
  return config;
}
