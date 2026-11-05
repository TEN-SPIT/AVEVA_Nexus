using System;
using AVEVA_Nexus.Config;

namespace Nexus.Tests
{
    public static class ReadOnlyModeTests
    {
        static void WithEnv(string value, Action body)
        {
            string old = Environment.GetEnvironmentVariable("NEXUS_READONLY");
            try
            {
                Environment.SetEnvironmentVariable("NEXUS_READONLY", value);
                body();
            }
            finally
            {
                Environment.SetEnvironmentVariable("NEXUS_READONLY", old);
            }
        }

        public static void RunAll()
        {
            Program.Run("ReadOnlyMode_is_on_when_variable_is_not_set", () =>
                WithEnv(null, () => Check.True(ReadOnlyMode.IsOn, "unset")));

            Program.Run("ReadOnlyMode_is_off_only_with_exactly_0", () =>
                WithEnv("0", () => Check.True(!ReadOnlyMode.IsOn, "0")));

            Program.Run("EnsureWritable_throws_403_with_the_documented_message_when_on", () =>
                WithEnv(null, () =>
                {
                    var ex = Check.Throws<AVEVA_Nexus.Exceptions.NexusException>(() => ReadOnlyMode.EnsureWritable(), "read-only blocks");
                    if (ex != null)
                    {
                        Check.Equal(403, ex.StatusCode, "status");
                        Check.Equal("Nexus is in read-only mode (set NEXUS_READONLY=0 to allow PML execution)", ex.Message, "message");
                    }
                }));

            Program.Run("EnsureWritable_does_nothing_when_off", () =>
                WithEnv("0", () => ReadOnlyMode.EnsureWritable()));

            Program.Run("SetWritable_true_turns_read_only_off_for_the_running_process", () =>
                WithEnv(null, () =>
                {
                    ReadOnlyMode.SetWritable(true);
                    Check.True(!ReadOnlyMode.IsOn, "writable after SetWritable(true)");
                    ReadOnlyMode.EnsureWritable();
                }));

            Program.Run("SetWritable_false_puts_read_only_back_on", () =>
                WithEnv("0", () =>
                {
                    ReadOnlyMode.SetWritable(false);
                    Check.True(ReadOnlyMode.IsOn, "read-only after SetWritable(false)");
                    Check.Throws<AVEVA_Nexus.Exceptions.NexusException>(() => ReadOnlyMode.EnsureWritable(), "blocked again");
                }));

            Program.Run("SetWritable_false_overrides_an_environment_that_allowed_writing", () =>
                WithEnv("0", () =>
                {
                    ReadOnlyMode.SetWritable(false);
                    Check.Equal("1", Environment.GetEnvironmentVariable("NEXUS_READONLY"), "variable");
                }));

            Program.Run("ReadOnlyMode_stays_on_for_any_other_value", () =>
            {
                foreach (var v in new[] { "1", "", "false", "no", "00", " 0" })
                    WithEnv(v, () => Check.True(ReadOnlyMode.IsOn, "value '" + v + "'"));
            });
        }
    }
}
