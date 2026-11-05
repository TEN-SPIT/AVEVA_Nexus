# NexusMCP: start Nexus from a button in E3D

Adds a **Nexus MCP** tab to E3D Design with two buttons, **Iniciar Nexus** and **Parar Nexus**. Starting
Nexus from a graphical session lets Claude (through the MCP server in `mcp/`) work in the session you
already have open, with your login and your open MDB. Nothing asks for a password.

```
NexusMCP/
  functions/   nexusstart, nexusstop, nexusroot, nexusmsg   (PML)
  dll/         AVEVA_Nexux_Addin.dll, Newtonsoft.Json.dll   (added by scripts\package-pmllib.ps1)
  uic/         NexusMCP.uic                                  (the tab and the two buttons)
```

## Install
1. Build the folder: `.\scripts\package-pmllib.ps1` (copies the PML, the `.uic` and the DLLs to
   `..\AVEVA_Nexus_local\package\NexusMCP`).
2. Copy `NexusMCP` into a folder that is on the project `PMLLIB`, for example `2_Design\` of the project
   customization PMLLIB. Nothing in it uses fixed paths: `!!nexusroot()` finds the folder from where
   `nexusstart.pmlfnc` is indexed.
3. In E3D run `PML REHASH ALL` (or restart). From now on `!!nexusstart()` and `!!nexusstop()` work from the
   command window.
4. To get the **buttons**, the `.uic` must be listed in a `DesignCustomization.xml` that is on the
   `CAF_UIC_PATH` of the project (for DEMO: `...\DEMO\ADMIN\Customization\Uic\DesignCustomization.xml`). Add
   one line inside `<UICustomizationFiles>`, with the real path of the installed folder:

   ```xml
   <CustomizationFile Name="NexusMCP" Path="\\<server>\<project>\ADMIN\Customization\PMLLIB\2_Design\NexusMCP\uic\NexusMCP.uic"/>
   ```

   That file is shared by every user of the project, so everyone will see the tab. Restart E3D to see it.
   Until then, the same thing works by typing the two functions.

## What the buttons do
- **Iniciar Nexus** starts the HTTP gateway at `http://localhost:5001/` (port from `!!nexusPort`, or the
  `NEXUS_PORT` environment variable) **with write permission**, using the login of the session. It is
  `!!nexusstart(TRUE)`. If Nexus is already running (also if it was started with the old
  `Start-Nexus-GUI.pmlmac` macro) it is not started again: it only switches the mode.
- **Nexus solo lectura** is `!!nexusstart(FALSE)`: starts it, or puts it back, in read-only mode.
- **Parar Nexus** stops the gateway and leaves it read-only. It does not save or release claims; if Claude
  changed the model, ask it to run `save_work` first.

Write permission only opens the door in Nexus. What Claude may actually write is decided by the MCP server:
only the databases listed as `write_dbs` for the open MDB, and saving goes only through its `save_work`.
Since Claude then works inside **your** session, remember that `save_work` saves the pending changes of that
whole database (also yours) and releases all the claims you hold on saved elements.

Switching the mode of a running session needs the **new DLL** (`HostConfig.SetWritable`, `IsRunning`). With the
old one the button says so and Nexus stays read-only; E3D must be restarted with the new DLL (the DLL of a
running session is locked, so it cannot be replaced while E3D is open).

Do not run it together with a headless session (`start_e3d`): both use port 5001.

## Status
Written following the usual way projects add buttons (`<Project>_Common.uic`).

Verified:
- The C# changes (`ReadOnlyMode.SetWritable` with unit tests, an idempotent `Start()` that no longer breaks
  a running server, `HostConfig.IsRunning/SetWritable`) build and the unit tests pass.
- The `.uic` is valid XML with unique keys.
- In a headless session: `!!nexusroot()` finds the folder, `!!nexusisrunning()` recognises a server started by a
  macro, `!!nexusstart(TRUE)` on a running server only applies the mode, and calling `Start()` twice is harmless.

**Not verified yet**, so do not rely on it:
- The final version of the functions end to end: stop followed by start (including the port being read from
  `!!nexusPort`) and `!!nexusstart(FALSE)` followed by blocked PML. The test session was running an older
  version of `nexusstart` that E3D did not reload, and could not be restarted in time.
- The **buttons** themselves, and any use inside a graphical session: Nexus runs its commands from its own
  thread and how a graphical E3D takes that is unknown. Save your work first and start with queries only.

PML notes learnt on the way: variable names cannot contain `_`; `.unset()` fails on objects from a DLL and the
error is swallowed by `handle any`; `elsehandle none` behaves differently after a previous error, so the
functions use "default value, try, `handle any` only to recover"; and E3D can keep running an older version of
a function it already loaded even after the file changed.
