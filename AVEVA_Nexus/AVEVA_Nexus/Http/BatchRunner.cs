using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Logging;
using AVEVA_Nexus.Models;
using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AVEVA_Nexus.Http
{
    // POST /api/Batch: runs up to MaxRequests sub-requests in order through the same routing function (so the same
    // read-only rules apply) and returns one {Status, Data, Error} per sub-request. A failing sub-request does not
    // fail the batch. Structural problems (too many requests, a nested Batch, no requests) fail the whole batch
    // with 400 before anything runs. Pure logic: no Aveva.* references.
    public static class BatchRunner
    {
        public const int MaxRequests = 50;

        public static List<BatchItemResult> Run(string body, Func<RouteContext, object> route)
        {
            BatchRequest request = JsonBody.Parse<BatchRequest>(body);
            if (request == null || request.Requests == null || request.Requests.Count == 0)
            {
                throw new NexusException("requests is required", 400);
            }
            if (request.Requests.Count > MaxRequests)
            {
                throw new NexusException("too many requests in a batch (max " + MaxRequests + ")", 400);
            }
            for (int i = 0; i < request.Requests.Count; i++)
            {
                BatchSubRequest sub = request.Requests[i];
                if (sub != null && IsBatchPath(sub.Path))
                {
                    throw new NexusException("requests[" + i + "]: nested Batch is not allowed", 400);
                }
            }

            var results = new List<BatchItemResult>();
            foreach (BatchSubRequest sub in request.Requests)
            {
                results.Add(RunOne(sub, route));
            }
            return results;
        }

        static BatchItemResult RunOne(BatchSubRequest sub, Func<RouteContext, object> route)
        {
            try
            {
                RouteContext context = ToContext(sub);
                object result = route(context);
                var response = result as ApiResponse;
                if (response == null)
                {
                    return new BatchItemResult { Status = 200, Data = result };
                }
                return new BatchItemResult
                {
                    Status = 200,
                    Data = response.Data,
                    Error = response.Success ? null : (response.Error ?? "failed")
                };
            }
            catch (NexusException ex)
            {
                return new BatchItemResult { Status = ex.StatusCode, Error = ex.Message };
            }
            catch (Exception ex)
            {
                NexusLogger.Error(ex);
                return new BatchItemResult { Status = 500, Error = "Internal Server Error" };
            }
        }

        static bool IsBatchPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return false;
            string[] segments = SplitPath(path).Trim('/').Split('/');
            return segments.Length >= 2 && segments[1].Equals("Batch", StringComparison.OrdinalIgnoreCase);
        }

        static string SplitPath(string path)
        {
            int q = path.IndexOf('?');
            return (q >= 0 ? path.Substring(0, q) : path).Trim();
        }

        public static RouteContext ToContext(BatchSubRequest sub)
        {
            if (sub == null) throw new NexusException("request is empty", 400);

            string method = (sub.Method ?? "GET").Trim().ToUpperInvariant();
            if (method != "GET" && method != "POST")
            {
                throw new NexusException("method must be GET or POST", 400);
            }
            if (string.IsNullOrWhiteSpace(sub.Path) ||
                !sub.Path.Trim().StartsWith("/api/", StringComparison.OrdinalIgnoreCase))
            {
                throw new NexusException("path must start with /api/", 400);
            }

            string raw = sub.Path.Trim();
            var query = new NameValueCollection(StringComparer.OrdinalIgnoreCase);
            int q = raw.IndexOf('?');
            if (q >= 0)
            {
                foreach (string part in raw.Substring(q + 1).Split('&'))
                {
                    if (part.Length == 0) continue;
                    int eq = part.IndexOf('=');
                    string key = Unescape(eq >= 0 ? part.Substring(0, eq) : part);
                    string value = eq >= 0 ? Unescape(part.Substring(eq + 1)) : "";
                    query[key] = value;
                }
            }
            if (sub.Query != null)
            {
                foreach (var pair in sub.Query)
                {
                    query[pair.Key] = TokenText(pair.Value);
                }
            }

            string body = "";
            if (sub.Body != null && sub.Body.Type != JTokenType.Null)
            {
                body = sub.Body.Type == JTokenType.String ? sub.Body.Value<string>() : sub.Body.ToString(Formatting.None);
            }

            return new RouteContext { Method = method, Path = SplitPath(raw), Query = query, Body = body };
        }

        static string Unescape(string s)
        {
            return Uri.UnescapeDataString(s.Replace('+', ' '));
        }

        static string TokenText(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null) return null;
            if (token.Type == JTokenType.String) return token.Value<string>();
            if (token.Type == JTokenType.Boolean) return token.Value<bool>() ? "true" : "false";
            if (token is JValue) return Convert.ToString(((JValue)token).Value, System.Globalization.CultureInfo.InvariantCulture);
            return token.ToString(Formatting.None);
        }
    }
}
