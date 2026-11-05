using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Logging;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Validation;
using Newtonsoft.Json;
using System;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AVEVA_Nexus.Http
{
    public class SimpleHttpServer
    {
        private HttpListener _listener;
        private CancellationTokenSource _cts;

        public static string Prefix = "http://localhost:5000/";

        // Shared secret expected in the X-Nexus-Token header (env NEXUS_TOKEN).
        private static string Token =>
            Environment.GetEnvironmentVariable("NEXUS_TOKEN");

        // Requests processed at once (they queue on AvevaGate anyway); above it the answer is 429.
        public const int MaxInFlight = 16;
        private static readonly RequestGuards.InFlightLimiter InFlight = new RequestGuards.InFlightLimiter(MaxInFlight);

        public bool IsRunning
        {
            get
            {
                try { return _listener != null && _listener.IsListening; }
                catch (ObjectDisposedException) { return false; }
            }
        }

        public void Start()
        {
            // Starting twice used to replace the listener and the cancellation token under the loop that
            // was already serving, which broke it. Starting a running gateway is now a no-op.
            if (IsRunning)
            {
                Console.WriteLine("HTTP Server already running, Start ignored");
                return;
            }

            // Exact loopback host: "+", "*" or a lookalike such as localhost.example.com is not local.
            bool localOnly = RequestGuards.IsLocalPrefix(Prefix);

            if (!localOnly && string.IsNullOrEmpty(Token))
                throw new InvalidOperationException(
                    "NEXUS_TOKEN must be set when listening on non-local prefixes.");

            var cts = new CancellationTokenSource();
            var listener = new HttpListener();
            listener.Prefixes.Add(Prefix);
            listener.Start();   // throws if the port is taken; the fields below are only set on success

            _cts = cts;
            _listener = listener;

            // The loop gets its own listener and token, so a Stop followed by a Start cannot
            // leave an old loop running against the new listener.
            Task.Run(() => ListenLoop(listener, cts));

            Console.WriteLine( "HTTP Server Started : " + Prefix);
        }

        public void Stop()
        {
            _cts?.Cancel();

            if (_listener != null)
            {
                _listener.Stop();
                _listener.Close();
            }

            Console.WriteLine("HTTP Server Stopped");
        }

        private async Task ListenLoop(HttpListener listener, CancellationTokenSource cts)
        {
            while (!cts.IsCancellationRequested)
            {
                try
                {
                    var context =
                        await listener.GetContextAsync();

                    _ = Task.Run(
                        () => ProcessRequest(context));
                }
                catch (Exception ex)
                {
                    Trace.Line(ex.Message);
                    bool stillListening;
                    try { stillListening = listener.IsListening; }
                    catch (ObjectDisposedException) { stillListening = false; }
                    if (!stillListening) break;   // closed under us: do not spin on the error
                }
            }
        }

        private async Task ProcessRequest(
            HttpListenerContext context)
        {
            try
            {
                Trace.Line("========================================");
                Trace.Line("HTTP REQUEST RECEIVED");
                Trace.Line("========================================");

                Trace.Line($"RawUrl       : {context.Request.RawUrl}");
                Trace.Line($"AbsolutePath : {context.Request.Url.AbsolutePath}");
                Trace.Line($"HttpMethod   : {context.Request.HttpMethod}");
                Trace.Line($"Url          : {context.Request.Url}");

                string path =
                    context.Request.Url.AbsolutePath;

                // CORS: lets browser-based clients (which send a preflight OPTIONS because of the custom
                // X-Nexus-Token header) talk to the gateway, but only when a token protects it. Without a
                // token, a request that declares a browser Origin is refused (see CorsPolicy).
                string token = Token;
                if (!CorsPolicy.IsOriginAllowed(token, context.Request.Headers["Origin"]))
                {
                    await WriteResponse(context, 403, new ApiResponse
                    {
                        Success = false,
                        Error = "Cross-origin requests are refused when NEXUS_TOKEN is not set"
                    });
                    return;
                }

                if (CorsPolicy.SendCorsHeaders(token))
                {
                    context.Response.Headers["Access-Control-Allow-Origin"] = "*";
                    context.Response.Headers["Access-Control-Allow-Headers"] = "X-Nexus-Token, Content-Type";
                    context.Response.Headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
                }

                if (context.Request.HttpMethod == "OPTIONS")
                {
                    context.Response.StatusCode = 204;
                    context.Response.Close();
                    return;
                }

                bool isHealth = path.TrimEnd('/').Equals("/api/Health", StringComparison.OrdinalIgnoreCase);

                if (!isHealth && !string.IsNullOrEmpty(token)
                    && !RequestGuards.TokenMatches(token, context.Request.Headers["X-Nexus-Token"]))
                {
                    await WriteResponse(context, 401, new ApiResponse
                    {
                        Success = false,
                        Error = "Unauthorized"
                    });
                    return;
                }

                // Health needs no E3D access: answer it without building a Router (whose controllers create providers).
                if (isHealth)
                {
                    await WriteResponse(context, 200, new ApiResponse { Success = true, Data = HealthInfo.Current() });
                    return;
                }

                if (!InFlight.TryEnter())
                {
                    await WriteResponse(context, 429, new ApiResponse
                    {
                        Success = false,
                        Error = "Too many requests in progress (max " + MaxInFlight + "); try again later"
                    });
                    return;
                }
                try
                {
                    await Dispatch(context, path);
                }
                finally
                {
                    InFlight.Exit();
                }
            }
            catch (NexusException ex)
            {
                NexusLogger.Error(ex);

                await WriteResponse(
                    context,
                    ex.StatusCode,
                    new ApiResponse
                    {
                        Success = false,
                        Error = ex.Message
                    });
            }
            catch (Exception ex)
            {
                NexusLogger.Error(ex);

                await WriteResponse(
                    context,
                    500,
                    new ApiResponse
                    {
                        Success = false,
                        Error = "Internal Server Error"
                    });
            }
        }

        private async Task Dispatch(HttpListenerContext context, string path)
        {
            NexusLogger.Info(
                $"{context.Request.HttpMethod} {path}");

            var router = new Router();

            string body = await ReadBody( context.Request);

            var routeContext = new RouteContext
            {
                Method = context.Request.HttpMethod,
                Path = context.Request.Url.AbsolutePath,
                Query = RequestGuards.ParseQuery(context.Request.RawUrl),
                Body = body,
            };

            var result = router.Route(routeContext);

            await WriteResponse(
                context,
                200,
                result);
        }

        private async Task WriteResponse(
            HttpListenerContext context,
            int statusCode,
            object payload)
        {
            string json =
                JsonConvert.SerializeObject(
                    payload,
                    Formatting.Indented);

            byte[] buffer =
                Encoding.UTF8.GetBytes(json);

            context.Response.StatusCode =
                statusCode;

            context.Response.ContentType =
                "application/json; charset=utf-8";

            context.Response.ContentLength64 =
                buffer.Length;

            await context.Response.OutputStream
                .WriteAsync(
                    buffer,
                    0,
                    buffer.Length);

            context.Response.Close();
        }

        private async Task<string> ReadBody(
            HttpListenerRequest request)
        {
            // Bounded (NEXUS_MAX_BODY, default 2 MB -> 413) and UTF-8 unless the Content-Type names a charset.
            return await RequestGuards.ReadBodyAsync(
                request.InputStream,
                request.ContentLength64,
                RequestGuards.MaxBodyBytes(Environment.GetEnvironmentVariable("NEXUS_MAX_BODY")),
                RequestGuards.BodyEncoding(request.ContentType));
        }
    }
}