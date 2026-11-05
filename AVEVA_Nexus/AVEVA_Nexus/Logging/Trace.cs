using System;
using System.Collections.Concurrent;
using System.Threading;

namespace AVEVA_Nexus.Logging
{
    // Per-request diagnostics. OFF by default: writing to E3D's console from request threads blocks them
    // whenever the console window is in "Select" (QuickEdit) mode, which freezes the whole gateway (seen in a
    // headless session: one click inside the console hung every request, even /api/Health). Set NEXUS_DEBUG=1
    // to get the trace back. Even then a request thread never writes to the console itself: lines go to a
    // bounded queue drained by one background thread, and are dropped (not waited for) when the queue is full,
    // so a frozen console can only lose trace lines, never stall a request.
    // Pure logic: no Aveva.* references (compiled by the unit-test runner).
    public static class Trace
    {
        public const string VariableName = "NEXUS_DEBUG";
        public const int QueueCapacity = 10000;

        static readonly BlockingCollection<string> Queue = new BlockingCollection<string>(QueueCapacity);
        static readonly object StartLock = new object();
        static Thread _writer;
        static int _pending;   // queued + being written

        public static bool Enabled
        {
            get { return Environment.GetEnvironmentVariable(VariableName) == "1"; }
        }

        public static void Line(string message)
        {
            if (!Enabled) return;
            EnsureWriter();
            Interlocked.Increment(ref _pending);
            if (!Queue.TryAdd(message))
            {
                Interlocked.Decrement(ref _pending);   // full (console frozen?): drop the line
            }
        }

        // Waits (up to timeoutMs) until every queued line has been written. For tests and shutdown only.
        public static bool Flush(int timeoutMs = 2000)
        {
            DateTime until = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (Volatile.Read(ref _pending) > 0)
            {
                if (DateTime.UtcNow > until) return false;
                Thread.Sleep(1);
            }
            return true;
        }

        static void EnsureWriter()
        {
            if (_writer != null) return;
            lock (StartLock)
            {
                if (_writer != null) return;
                var t = new Thread(WriteLoop) { IsBackground = true, Name = "nexus-trace" };
                t.Start();
                _writer = t;
            }
        }

        static void WriteLoop()
        {
            foreach (string line in Queue.GetConsumingEnumerable())
            {
                try { Console.WriteLine(line); }
                catch (Exception) { }
                finally { Interlocked.Decrement(ref _pending); }
            }
        }
    }
}
