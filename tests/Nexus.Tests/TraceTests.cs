using System;
using System.IO;
using AVEVA_Nexus.Logging;

namespace Nexus.Tests
{
    public static class TraceTests
    {
        // A console in QuickEdit "Select" mode: every write blocks until released.
        sealed class BlockingWriter : TextWriter
        {
            public readonly System.Threading.ManualResetEventSlim Release = new System.Threading.ManualResetEventSlim(false);
            public override System.Text.Encoding Encoding { get { return System.Text.Encoding.UTF8; } }
            public override void Write(char value) { Release.Wait(); }
            public override void Write(string value) { Release.Wait(); }
            public override void WriteLine(string value) { Release.Wait(); }
        }

        static string Capture(string debug, Action body)
        {
            string old = Environment.GetEnvironmentVariable("NEXUS_DEBUG");
            var oldOut = Console.Out;
            var sw = new StringWriter();
            try
            {
                Environment.SetEnvironmentVariable("NEXUS_DEBUG", debug);
                Console.SetOut(sw);
                body();
                Trace.Flush();
            }
            finally
            {
                Console.SetOut(oldOut);
                Environment.SetEnvironmentVariable("NEXUS_DEBUG", old);
            }
            return sw.ToString();
        }

        public static void RunAll()
        {
            Program.Run("Trace_writes_nothing_to_the_console_by_default", () =>
                Check.Equal("", Capture(null, () => Trace.Line("hello")), "unset"));

            Program.Run("Trace_stays_silent_for_any_value_other_than_1", () =>
            {
                foreach (var v in new[] { "0", "", "true", "yes" })
                    Check.Equal("", Capture(v, () => Trace.Line("hello")), "value '" + v + "'");
            });

            Program.Run("Trace_writes_the_line_when_NEXUS_DEBUG_is_1", () =>
                Check.Equal("hello" + Environment.NewLine, Capture("1", () => Trace.Line("hello")), "enabled"));

            Program.Run("Trace_does_not_block_the_caller_when_the_console_is_stuck", () =>
            {
                var stuck = new BlockingWriter();
                string old = Environment.GetEnvironmentVariable("NEXUS_DEBUG");
                var oldOut = Console.Out;
                try
                {
                    Environment.SetEnvironmentVariable("NEXUS_DEBUG", "1");
                    Console.SetOut(stuck);
                    var sw = System.Diagnostics.Stopwatch.StartNew();
                    for (int i = 0; i < 100; i++) Trace.Line("x");
                    Check.True(sw.ElapsedMilliseconds < 1000, "100 lines took " + sw.ElapsedMilliseconds + " ms");
                }
                finally
                {
                    stuck.Release.Set();
                    Trace.Flush();
                    Console.SetOut(oldOut);
                    Environment.SetEnvironmentVariable("NEXUS_DEBUG", old);
                }
            });

            Program.Run("NexusLogger_goes_through_Trace_so_it_is_silent_by_default", () =>
                Check.Equal("", Capture(null, () => { NexusLogger.Info("x"); NexusLogger.Error(new Exception("e")); }), "logger"));
        }
    }
}
