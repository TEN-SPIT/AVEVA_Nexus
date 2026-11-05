using System;
using System.IO;
using AVEVA_Nexus.Logging;

namespace Nexus.Tests
{
    public static class TraceTests
    {
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

            Program.Run("NexusLogger_goes_through_Trace_so_it_is_silent_by_default", () =>
                Check.Equal("", Capture(null, () => { NexusLogger.Info("x"); NexusLogger.Error(new Exception("e")); }), "logger"));
        }
    }
}
