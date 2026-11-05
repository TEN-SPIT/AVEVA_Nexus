using System;

namespace AVEVA_Nexus.Logging
{
    public static class NexusLogger
    {
        public static void Info(string message)
        {
            Trace.Line(
                $"[{DateTime.Now:HH:mm:ss}] [INFO] {message}");
        }

        public static void Error(Exception ex)
        {
            Trace.Line(
                $"[{DateTime.Now:HH:mm:ss}] [ERROR] {ex}");
        }
    }
}