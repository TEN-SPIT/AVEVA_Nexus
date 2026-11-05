using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using Newtonsoft.Json;

namespace Nexus.Tests
{
    public static class HealthInfoTests
    {
        public static void RunAll()
        {
            Program.Run("Health_reports_ok_version_and_gate_state", () =>
            {
                AvevaGate.Reset();
                HealthInfo h = HealthInfo.Current();
                Check.Equal("ok", h.Status, "status");
                Check.True(!string.IsNullOrEmpty(h.Version), "version present");
                Check.True(h.Gate != null, "gate");
                Check.Equal("direct", h.Gate.Mode, "gate mode");
                string json = JsonConvert.SerializeObject(h);
                Check.True(json.Contains("\"Version\"") && json.Contains("\"Busy\""), "serialised: " + json);
            });

            Program.Run("Version_uses_the_informational_version_if_present_else_the_assembly_version", () =>
            {
                // The test assembly has no AssemblyInformationalVersion: falls back to its AssemblyVersion.
                var asm = typeof(HealthInfoTests).Assembly;
                Check.Equal(asm.GetName().Version.ToString(), HealthInfo.VersionOf(asm), "fallback");
                // Newtonsoft.Json carries one.
                string nj = HealthInfo.VersionOf(typeof(JsonConvert).Assembly);
                Check.True(nj.StartsWith("13."), "informational: " + nj);
            });
        }
    }
}
