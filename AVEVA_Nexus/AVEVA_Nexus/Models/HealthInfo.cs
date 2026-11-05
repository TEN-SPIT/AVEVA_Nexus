using AVEVA_Nexus.Integration;
using System.Reflection;

namespace AVEVA_Nexus.Models
{
    // Data of GET /api/Health (no token needed, so nothing sensitive: add-in version and gate state only).
    // Pure logic: no Aveva.* references.
    public class HealthInfo
    {
        public string Status { get; set; }
        public string Version { get; set; }
        public GateState Gate { get; set; }

        public static HealthInfo Current()
        {
            return new HealthInfo
            {
                Status = "ok",
                Version = VersionOf(typeof(HealthInfo).Assembly),
                Gate = AvevaGate.State(),
            };
        }

        // AssemblyInformationalVersion if the build stamped one (e.g. "1.2.0+abc1234"), else AssemblyVersion.
        public static string VersionOf(Assembly assembly)
        {
            var info = (AssemblyInformationalVersionAttribute)System.Attribute.GetCustomAttribute(
                assembly, typeof(AssemblyInformationalVersionAttribute));
            if (info != null && !string.IsNullOrWhiteSpace(info.InformationalVersion))
            {
                return info.InformationalVersion;
            }
            return assembly.GetName().Version.ToString();
        }
    }
}
