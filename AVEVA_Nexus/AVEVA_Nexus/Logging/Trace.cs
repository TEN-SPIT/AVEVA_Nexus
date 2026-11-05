using System;

namespace AVEVA_Nexus.Logging
{
    // Per-request diagnostics. OFF by default: writing to E3D's console from request threads blocks them
    // whenever the console window is in "Select" (QuickEdit) mode, which freezes the whole gateway (seen in a
    // headless session: one click inside the console hung every request, even /api/Health). Set NEXUS_DEBUG=1
    // to get the trace back. Pure logic: no Aveva.* references (compiled by the unit-test runner).
    public static class Trace
    {
        public const string VariableName = "NEXUS_DEBUG";

        public static bool Enabled
        {
            get { return Environment.GetEnvironmentVariable(VariableName) == "1"; }
        }

        public static void Line(string message)
        {
            if (Enabled) Console.WriteLine(message);
        }
    }
}
