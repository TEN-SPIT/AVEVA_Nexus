using System.IO;
using System.Text;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Validation;

namespace Nexus.Tests
{
    public static class RequestGuardsTests
    {
        static string Read(byte[] bytes, long declared, int max, string contentType)
        {
            using (var stream = new MemoryStream(bytes))
            {
                return RequestGuards.ReadBodyAsync(stream, declared, max, RequestGuards.BodyEncoding(contentType)).Result;
            }
        }

        static int Status(System.Action act, string name)
        {
            try { act(); }
            catch (System.AggregateException ex) when (ex.InnerException is NexusException)
            {
                return ((NexusException)ex.InnerException).StatusCode;
            }
            catch (NexusException ex) { return ex.StatusCode; }
            Check.Fail(name, "nothing thrown");
            return 0;
        }

        public static void RunAll()
        {
            Program.Run("LocalPrefix_accepts_only_loopback_hosts", () =>
            {
                Check.True(RequestGuards.IsLocalPrefix("http://localhost:5000/"), "localhost");
                Check.True(RequestGuards.IsLocalPrefix("http://LOCALHOST:5000/"), "case");
                Check.True(RequestGuards.IsLocalPrefix("http://127.0.0.1:5000/"), "ipv4");
                Check.True(RequestGuards.IsLocalPrefix("http://[::1]:5000/"), "ipv6");
                Check.True(RequestGuards.IsLocalPrefix("https://localhost/"), "no port");
            });

            Program.Run("LocalPrefix_rejects_wildcards_and_lookalike_hosts", () =>
            {
                Check.True(!RequestGuards.IsLocalPrefix("http://+:5000/"), "+");
                Check.True(!RequestGuards.IsLocalPrefix("http://*:5000/"), "*");
                Check.True(!RequestGuards.IsLocalPrefix("http://localhost.evil.com:5000/"), "localhost.evil.com");
                Check.True(!RequestGuards.IsLocalPrefix("http://127.0.0.1.nip.io:5000/"), "127.0.0.1.nip.io");
                Check.True(!RequestGuards.IsLocalPrefix("http://10.0.0.5:5000/"), "lan ip");
                Check.True(!RequestGuards.IsLocalPrefix("http://myhost:5000/"), "hostname");
                Check.True(!RequestGuards.IsLocalPrefix(null), "null");
                Check.True(!RequestGuards.IsLocalPrefix("garbage"), "garbage");
            });

            Program.Run("TokenMatches_is_exact", () =>
            {
                Check.True(RequestGuards.TokenMatches("s3cret", "s3cret"), "same");
                Check.True(!RequestGuards.TokenMatches("s3cret", "s3cre"), "prefix");
                Check.True(!RequestGuards.TokenMatches("s3cret", "s3cretX"), "longer");
                Check.True(!RequestGuards.TokenMatches("s3cret", "S3cret"), "case");
                Check.True(!RequestGuards.TokenMatches("s3cret", null), "missing");
                Check.True(!RequestGuards.TokenMatches("s3cret", ""), "empty");
            });

            Program.Run("BodyEncoding_is_utf8_unless_a_charset_is_given", () =>
            {
                Check.Equal(65001, RequestGuards.BodyEncoding(null).CodePage, "none");
                Check.Equal(65001, RequestGuards.BodyEncoding("application/json").CodePage, "no charset");
                Check.Equal(28591, RequestGuards.BodyEncoding("application/json; charset=ISO-8859-1").CodePage, "latin1");
                Check.Equal(65001, RequestGuards.BodyEncoding("text/plain; charset=\"utf-8\"").CodePage, "quoted");
                Check.Equal(65001, RequestGuards.BodyEncoding("application/json; charset=nonsense").CodePage, "unknown falls back");
            });

            Program.Run("ReadBody_decodes_utf8_without_charset", () =>
            {
                byte[] bytes = Encoding.UTF8.GetBytes("{\"name\":\"/CAÑERÍA-1\"}");
                Check.Equal("{\"name\":\"/CAÑERÍA-1\"}", Read(bytes, bytes.Length, 1024, "application/json"), "text");
            });

            Program.Run("ReadBody_drops_a_utf8_bom", () =>
            {
                byte[] bytes = new byte[] { 0xEF, 0xBB, 0xBF, (byte)'{', (byte)'}' };
                Check.Equal("{}", Read(bytes, -1, 1024, null), "no bom");
            });

            Program.Run("ReadBody_refuses_a_declared_length_over_the_limit_with_413", () =>
                Check.Equal(413, Status(() => Read(new byte[10], 5000, 100, null), "declared"), "status"));

            Program.Run("ReadBody_refuses_a_chunked_body_over_the_limit_with_413", () =>
                Check.Equal(413, Status(() => Read(new byte[101], -1, 100, null), "streamed"), "status"));

            Program.Run("ReadBody_accepts_a_body_exactly_at_the_limit", () =>
                Check.Equal(100, Read(Encoding.ASCII.GetBytes(new string('a', 100)), -1, 100, null).Length, "length"));

            Program.Run("MaxBodyBytes_defaults_to_2MB_and_reads_NEXUS_MAX_BODY", () =>
            {
                Check.Equal(2 * 1024 * 1024, RequestGuards.MaxBodyBytes(null), "default");
                Check.Equal(2 * 1024 * 1024, RequestGuards.MaxBodyBytes("x"), "invalid");
                Check.Equal(2 * 1024 * 1024, RequestGuards.MaxBodyBytes("-5"), "negative");
                Check.Equal(4096, RequestGuards.MaxBodyBytes("4096"), "set");
            });

            Program.Run("ParseQuery_decodes_percent_escapes_as_utf8", () =>
            {
                var q = RequestGuards.ParseQuery("/api/Element/Children?dbref=%2FCA%C3%91O&type=ZONE&limit=5&flag&x=a+b");
                Check.Equal("/CAÑO", q["dbref"], "utf-8 name");
                Check.Equal("ZONE", q["TYPE"], "case-insensitive keys");
                Check.Equal("5", q["limit"], "limit");
                Check.Equal("", q["flag"], "key without value");
                Check.Equal("a b", q["x"], "plus is a space");
                Check.Equal(0, RequestGuards.ParseQuery("/api/Health").Count, "no query");
                Check.Equal(0, RequestGuards.ParseQuery(null).Count, "null");
            });

            Program.Run("InFlight_limiter_refuses_above_the_cap", () =>
            {
                var limiter = new RequestGuards.InFlightLimiter(2);
                Check.True(limiter.TryEnter(), "1");
                Check.True(limiter.TryEnter(), "2");
                Check.True(!limiter.TryEnter(), "3 refused");
                limiter.Exit();
                Check.True(limiter.TryEnter(), "slot freed");
            });
        }
    }
}
