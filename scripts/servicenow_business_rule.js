// ServiceNow Business Rule: "Trigger triage-agent on incident create"
// Table: incident | When: after | Insert: true | Update: false | Async: true
// Condition (example): severity != 1 AND cmdb_ci is not empty
//
// Prereqs on the instance:
//   sys_properties: triage.agent.url    = https://agent.example.com/trigger
//                   triage.agent.secret = <same secret as TRIAGE_WEBHOOK_SECRET>
// Payload carries ONLY the sys_id; the agent reads the ticket back itself.
(function executeRule(current /*GlideRecord*/, previous /*null on insert*/) {
    try {
        var url = gs.getProperty('triage.agent.url');
        var secret = gs.getProperty('triage.agent.secret');
        if (!url || !secret) {
            gs.warn('[triage-agent] url/secret properties not set; skipping');
            return;
        }
        var body = JSON.stringify({ sys_id: current.getUniqueValue() });

        // HMAC-SHA256 of the exact body, hex-encoded (matches agent verify).
        var mac = new GlideCertificateEncryption()
            .generateMac(GlideStringUtil.base64Encode(secret), 'HmacSHA256', body);
        // generateMac returns base64; agent expects hex - convert:
        var hex = '';
        var raw = GlideStringUtil.base64Decode(mac);
        for (var i = 0; i < raw.length; i++) {
            var h = raw.charCodeAt(i).toString(16);
            hex += (h.length === 1 ? '0' : '') + h;
        }

        var req = new sn_ws.RESTMessageV2();
        req.setEndpoint(url);
        req.setHttpMethod('POST');
        req.setRequestHeader('Content-Type', 'application/json');
        req.setRequestHeader('X-Triage-Signature', hex);
        req.setRequestBody(body);
        req.setHttpTimeout(10000);
        var resp = req.executeAsync();  // never block ticket creation
        gs.info('[triage-agent] trigger sent for ' + current.number);
    } catch (e) {
        // NEVER let trigger failure break incident creation.
        gs.error('[triage-agent] trigger failed: ' + e.message);
    }
})(current, previous);
