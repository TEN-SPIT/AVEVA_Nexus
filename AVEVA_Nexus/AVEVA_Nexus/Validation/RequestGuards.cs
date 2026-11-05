using AVEVA_Nexus.Exceptions;
using System;
using System.Collections.Specialized;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AVEVA_Nexus.Validation
{
    // Checks applied by SimpleHttpServer to every request: which listen prefixes count as local, token
    // comparison, request body size/encoding and the cap on concurrent requests.
    // Pure logic: must not reference Aveva.* (it is compiled by the unit-test runner).
    public static class RequestGuards
    {
        public const int DefaultMaxBodyBytes = 2 * 1024 * 1024;

        // True only when the HttpListener prefix binds a loopback host: localhost, 127.0.0.1 or [::1].
        // "+", "*" and any other host (including lookalikes such as localhost.example.com) are not local.
        public static bool IsLocalPrefix(string prefix)
        {
            if (string.IsNullOrWhiteSpace(prefix)) return false;
            string p = prefix.Trim();
            int scheme = p.IndexOf("://", StringComparison.Ordinal);
            if (scheme < 0) return false;
            string authority = p.Substring(scheme + 3);
            int slash = authority.IndexOf('/');
            if (slash >= 0) authority = authority.Substring(0, slash);

            string host;
            if (authority.StartsWith("["))
            {
                int close = authority.IndexOf(']');
                if (close < 0) return false;
                host = authority.Substring(1, close - 1);
            }
            else
            {
                int colon = authority.IndexOf(':');
                host = colon >= 0 ? authority.Substring(0, colon) : authority;
            }

            return host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
                || host == "127.0.0.1"
                || host == "::1";
        }

        // Constant-time comparison (does not stop at the first differing character).
        public static bool TokenMatches(string expected, string given)
        {
            if (expected == null || given == null) return false;
            byte[] a = Encoding.UTF8.GetBytes(expected);
            byte[] b = Encoding.UTF8.GetBytes(given);
            int diff = a.Length ^ b.Length;
            for (int i = 0; i < a.Length; i++)
            {
                diff |= a[i] ^ (i < b.Length ? b[i] : 0);
            }
            return diff == 0;
        }

        // The charset of the Content-Type if it names a known encoding, else UTF-8. (HttpListenerRequest's
        // ContentEncoding falls back to the ANSI code page, which garbled non-ASCII names.)
        public static Encoding BodyEncoding(string contentType)
        {
            if (!string.IsNullOrEmpty(contentType))
            {
                foreach (string part in contentType.Split(';'))
                {
                    string p = part.Trim();
                    if (!p.StartsWith("charset=", StringComparison.OrdinalIgnoreCase)) continue;
                    string name = p.Substring("charset=".Length).Trim().Trim('"', '\'');
                    try { return Encoding.GetEncoding(name); }
                    catch (ArgumentException) { break; }
                }
            }
            return new UTF8Encoding(false);
        }

        // NEXUS_MAX_BODY (bytes, > 0); default 2 MB.
        public static int MaxBodyBytes(string variableValue)
        {
            int value;
            return int.TryParse((variableValue ?? "").Trim(), out value) && value > 0 ? value : DefaultMaxBodyBytes;
        }

        // Reads at most maxBytes; more (declared or actually sent) is refused with 413.
        // declaredLength: Content-Length, or -1 when unknown (chunked).
        public static async Task<string> ReadBodyAsync(Stream stream, long declaredLength, int maxBytes, Encoding encoding)
        {
            if (declaredLength > maxBytes) throw TooLarge(maxBytes);

            var buffer = new MemoryStream();
            var chunk = new byte[8192];
            int read;
            while ((read = await stream.ReadAsync(chunk, 0, chunk.Length)) > 0)
            {
                if (buffer.Length + read > maxBytes) throw TooLarge(maxBytes);
                buffer.Write(chunk, 0, read);
            }

            string text = encoding.GetString(buffer.GetBuffer(), 0, (int)buffer.Length);
            return text.Length > 0 && text[0] == '﻿' ? text.Substring(1) : text;
        }

        // Query string of a raw URL, percent-decoded as UTF-8 (HttpListenerRequest.QueryString uses the same
        // ANSI fallback as ContentEncoding). Keys are case-insensitive; a repeated key keeps the last value.
        public static NameValueCollection ParseQuery(string rawUrl)
        {
            var query = new NameValueCollection(StringComparer.OrdinalIgnoreCase);
            int q = rawUrl == null ? -1 : rawUrl.IndexOf('?');
            if (q < 0) return query;
            foreach (string part in rawUrl.Substring(q + 1).Split('&'))
            {
                if (part.Length == 0) continue;
                int eq = part.IndexOf('=');
                string key = WebUtility.UrlDecode(eq >= 0 ? part.Substring(0, eq) : part);
                query[key] = eq >= 0 ? WebUtility.UrlDecode(part.Substring(eq + 1)) : "";
            }
            return query;
        }

        static NexusException TooLarge(int maxBytes)
        {
            return new NexusException("Request body too large (max " + maxBytes + " bytes)", 413);
        }

        // Caps the requests being processed at once; above it the server answers 429 instead of queueing.
        public sealed class InFlightLimiter
        {
            readonly SemaphoreSlim _slots;

            public InFlightLimiter(int max) { _slots = new SemaphoreSlim(max, max); }

            public bool TryEnter() { return _slots.Wait(0); }

            public void Exit() { _slots.Release(); }
        }
    }
}
