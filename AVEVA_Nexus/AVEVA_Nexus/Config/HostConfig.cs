using Aveva.Core.PMLNet;
using AVEVA_Nexus.Addin;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.NetworkInformation;
using System.Text;
using System.Threading.Tasks;
using AVEVA_Nexus.Http;
using AVEVA_Nexus.Integration;

namespace AVEVA_Nexus.Config
{
    [PMLNetCallable()]
    public class HostConfig
    {
        public static SimpleHttpServer _host = new SimpleHttpServer();

        [PMLNetCallable()]
        public HostConfig() { }

        [PMLNetCallable()]
        public void Assign(HostConfig that) { }

        [PMLNetCallable()]
        public void SetPrefix(string prefix = "http://localhost:5000/") {
            SimpleHttpServer.Prefix = prefix;
        }

        [PMLNetCallable()]
        public void Start()
        {
            // PML calls this on E3D's interface thread. In a graphical session capture its context so that
            // every E3D call is run there (see AvevaGate). Headless sessions have no context: unchanged.
            // ON by default when a context exists: in a graphical E3D, running model writes from a background
            // thread hung every later PML call (verified with the user: same NEW BOX gave a 500 and a permanent
            // hang in "direct" mode and worked in "ui-thread" mode). NEXUS_UI_DISPATCH=0 turns it off.
            // NEXUS_UI_DISPATCH: "0" off; unset/"auto"/"1" = on whenever this thread has a real message-loop
            // context (graphical session), direct otherwise; see AvevaGate.SelectUiContext.
            AvevaGate.CaptureUiContext(AvevaGate.SelectUiContext(
                Environment.GetEnvironmentVariable("NEXUS_UI_DISPATCH"),
                System.Threading.SynchronizationContext.Current));
            Console.WriteLine("Nexus dispatch mode: " + AvevaGate.Mode);
            _host.Start();
        }

        // "ui-thread" (E3D calls run on the interface thread) or "direct" (on the request thread).
        [PMLNetCallable()]
        public string DispatchMode()
        {
            return AvevaGate.Mode;
        }

        [PMLNetCallable()]
        public void Stop()
        {
            _host?.Stop();
        }

        // True while the gateway is listening (so a button can tell "already started" from "stopped").
        [PMLNetCallable()]
        public bool IsRunning()
        {
            return _host != null && _host.IsRunning;
        }

        // true: PML/Execute and PML/Run are allowed; false: read-only (the default).
        [PMLNetCallable()]
        public void SetWritable(bool writable)
        {
            ReadOnlyMode.SetWritable(writable);
        }
    }
}
