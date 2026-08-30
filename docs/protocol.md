# Honeywell Smile Connect — Protocol Reference

> Reverse-engineered against an SCN-10 gateway (server version 1.6.32687,
> relay SCN-10 V1.6 Rev. 12). This document is the working basis for the
> API layer in `custom_components/honeywell_smileconnect/api/`.

## 1. Login Flow

```
1. POST /api/user/token/challenge   Body: udid=web
   → { devicetoken: "<challenge-token>" }

2. Client computes:
   hashed = hash_auth_token(password, challenge_token)
   # Standard HeatApp: MD5(password + challenge_token)
   # Honeywell: PBKDF2/SHA512, with "stringToCharcodes" pre-processing
   #            -> TODO: exact parameters (iterations, salt, key length)
   #               not yet fully verified.

3. POST /api/user/token/response
   Body: {
     udid: "web",
     login: "<username>",
     token: "<challenge-token>",
     hashed: "<hash from step 2>",
     devicename: "Computer"
   }
   → { userid, devicetoken_encrypted }

4. devicetoken (plaintext) = AES-256-CBC-Decrypt(
     ciphertext = devicetoken_encrypted (Base64),
     key = SHA-256(password),
     iv  = Base64-decode("D3GC5NQEFH13is04KD2tOg==")   # TODO: verify on
                                                          # Honeywell whether
                                                          # this matches the
                                                          # standard HeatApp IV
   )
   Padding: standard HeatApp strips "\x10" padding manually
            (no standard PKCS7 handling in the reference implementation).
```

After a successful login, the client holds:
- `userid`
- `devicetoken` (plaintext, decrypted)
- `udid` = `"web"` (fixed, not generated)

## 2. Request Signing (for every authenticated call)

```
1. Sort all body parameters alphabetically by key.
2. Serialize values to strings:
   - Arrays: "[a,b,c]" (brackets, comma-separated, no spaces)
   - single-element array: just the value, no brackets
   - scalars: plain string representation
3. Build the data string: "key1=val1|key2=val2|...|"   (pipe, NOT &, with
   a trailing pipe)
4. Signature = MD5(data_string + devicetoken)
5. Insert additional required fields BEFORE computing the signature:
   udid, userid, reqcount  (reqcount incremented per request)
6. Final request body: all original parameters + udid + userid + reqcount
   + request_signature=<signature>, standard URL-encoded (& as the
   separator in the actual HTTP body — the pipe variant is ONLY used for
   the signature computation).
```

> ⚠️ Difference from the generic HeatApp protocol: standard HeatApp replaces
> `&` with `|` for the signature computation (see `apiRequest.py` in the
> legacy code: `.replace('&', '|')`), which is structurally equivalent to
> "building with pipes from the start". On Honeywell this was observed as
> pipe-native directly in the admin console code — functionally equivalent,
> but noted here as an implementation detail.

## 3. Known Endpoints

See `CLAUDE.md` for the full list. Additional raw data examples (fixtures)
will live under `tests/fixtures/` once added.

### `/api/scene/status` — example response (generic HeatApp, as a structural
template; the Honeywell response still needs to be captured 1:1)

```json
{
  "success": true,
  "scenes": [
    { "name": "Party", "min": 0, "max": 12, "step": 1, "isActive": false },
    { "name": "Boost", "min": 0, "max": 120, "step": 30, "isActive": false },
    { "name": "Holiday", "min": false, "max": false, "step": false, "isActive": false },
    { "name": "Shower", "min": 0, "max": 1440, "step": 1, "isActive": false },
    { "name": "Leave", "min": 0, "max": 12, "step": 1, "isActive": false },
    { "name": "Standby", "min": 0, "max": 1, "step": 1, "isActive": false },
    { "name": "Towel", "min": false, "max": false, "step": false }
  ]
}
```

The test installation has only one room ("Alle"/"All") and no Shower/Towel
usage — these scenes are kept in code as constants but remain untested.

## 4. Room Status Codes (observed, from preset mapping in the legacy code)

| Code | Meaning |
|---|---|
| 12 | Observed on a live Honeywell gateway room with `status: "new"` ("Regler MK1") - meaning not yet mapped to a preset, currently falls through to "no preset" |
| 43 | Party active |
| 46 | Boost active |
| 99 | Error state |
| 122, 51, 41, 131, 54, 137 | Manual/schedule mode (no preset) — exact meaning of individual codes not fully confirmed |
| 127 | Holiday active |
| 130 | Leave active |
| 132 | Standby active |

## 4b. Field availability differences vs. generic HeatApp

Confirmed on a live Honeywell gateway: `/api/room/list` room objects do
**not** always include `actualTemperature` - observed missing entirely on a
single-zone installation with no dedicated room sensor ("Regler MK1"/relay
controller). The integration treats it as optional (falls back to unknown)
rather than assuming it is always present, unlike the generic HeatApp
reference code this was originally ported from.

## 5. Open Items (as of project handover)

- [ ] Verify the exact PBKDF2/SHA512 parameters for password hashing
- [ ] Confirm the AES decrypt IV on Honeywell (identical to standard HeatApp?)
- [ ] Test `setrooms` behavior before scene activation (order dependency)
- [ ] Decimal temperature values (e.g. 20.5 °C) — dot vs. comma in the request
- [ ] Clarify the purpose of `/api/xpertonly/start`, `/admin/sentry/*`
