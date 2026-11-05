using AVEVA_Nexus.Exceptions;
using System;
using System.Threading;

namespace AVEVA_Nexus.Integration
{
    // E3D/PML is not thread-safe: simultaneous requests crashed the whole E3D session (observed in testing).
    // Every call into AVEVA goes through this single process-wide gate, which also serialises callers.
    //
    // Two modes:
    //  - "direct" (headless/TTY session, or no interface context captured): the work runs on the calling
    //    thread under a lock, as it always did.
    //  - "ui-thread" (graphical E3D): the work runs on E3D's interface thread. HostConfig.Start() is called
    //    from PML on that thread and captures its SynchronizationContext. Running model writes from a
    //    background thread hung a graphical E3D (Windows reported "stopped interacting"): writing refreshes
    //    the explorer and the 3D view, which belong to the interface thread.
    //
    // If the interface thread is blocked (a modal dialog, a long command) the request is NOT left hanging:
    // after UiTimeoutMs it fails with 503 and its work is skipped if it has not started yet.
    //
    // Pure logic: no Aveva.* references (it is compiled by the unit-test runner).
    public static class AvevaGate
    {
        static readonly object Lock = new object();
        static SynchronizationContext _ui;
        static int _uiThreadId;

        public static int UiTimeoutMs = 60000;

        public static string Mode { get { return _ui == null ? "direct" : "ui-thread"; } }

        // NEXUS_UI_DISPATCH: on unless it is exactly "0". A headless session has no SynchronizationContext, so
        // it stays "direct" whatever this says.
        public static bool UiDispatchEnabled(string variableValue)
        {
            return variableValue != "0";
        }

        // Called from the interface thread (HostConfig.Start). Null leaves the gate in direct mode.
        public static void CaptureUiContext(SynchronizationContext context)
        {
            lock (Lock)
            {
                _ui = context;
                _uiThreadId = context == null ? 0 : Thread.CurrentThread.ManagedThreadId;
            }
        }

        public static void Reset()
        {
            lock (Lock) { _ui = null; _uiThreadId = 0; UiTimeoutMs = 60000; }
        }

        public static T Run<T>(Func<T> work)
        {
            SynchronizationContext ui = _ui;

            if (ui == null)
            {
                lock (Lock) { return work(); }
            }

            // Already on the interface thread (a provider method calling another, from work this gate
            // posted). The caller that posted it holds Lock while waiting, so taking it here would
            // deadlock; and everything runs on this one thread anyway, so nothing can overlap.
            if (Thread.CurrentThread.ManagedThreadId == _uiThreadId)
            {
                return work();
            }

            // One request at a time reaches the interface thread; the lock is held while waiting for it.
            lock (Lock)
            {
                T result = default(T);
                Exception error = null;
                int state = 0;   // 0 queued, 1 started, 2 abandoned (timed out before it started)
                var done = new ManualResetEventSlim(false);

                ui.Post(_ =>
                {
                    if (Interlocked.CompareExchange(ref state, 1, 0) != 0) return;   // abandoned: skip
                    try { result = work(); }
                    catch (Exception ex) { error = ex; }
                    finally { done.Set(); }
                }, null);

                if (!done.Wait(UiTimeoutMs))
                {
                    if (Interlocked.CompareExchange(ref state, 2, 0) == 0)
                        throw new NexusException(
                            "E3D's interface thread did not respond in " + (UiTimeoutMs / 1000) +
                            " s (is a dialog open in E3D?). The request was not executed.", 503);
                    done.Wait();   // it had just started: wait for it rather than abandon a running write
                }

                if (error != null)
                {
                    var nexus = error as NexusException;
                    if (nexus != null) throw new NexusException(nexus.Message, nexus.StatusCode);
                    throw new InvalidOperationException(error.Message, error);
                }
                return result;
            }
        }
    }
}
