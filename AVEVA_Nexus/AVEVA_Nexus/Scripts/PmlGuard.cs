using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace AVEVA_Nexus.Scripts
{
    // Wraps the lines of a unit so a PML error is CAUGHT instead of reaching E3D's alert.
    //
    // Why: in a graphical E3D an unhandled PML error opens a modal "Error (47,15) ..." dialog; the request
    // waits for a person, and once the dialog is closed the interface stays blocked ("Not Responding"). A
    // headless (TTY) session has no dialog, which is why it only showed up in graphics.
    //
    // Verified on E3D 3.1 (see the vault notes):
    //  - a "handle any" protects only the statement just before it: a handle after a whole block, or at the
    //    end of a macro, catches nothing, so one is put after EACH statement;
    //  - inside the handler "return" ends the macro, which keeps the old behaviour (stop at the first
    //    failing line) so a later line never runs against the wrong current element;
    //  - "!!error.text" read inside the handler gives the real message ("PML: Function not found"); once a
    //    handler catches an error E3D no longer prints it, so this is the only place to get it.
    // A statement the user already followed with their own handle is left to that handle.
    //
    // Pure logic: must not reference Aveva.* (it is compiled by the unit-test runner).
    public static class PmlGuard
    {
        public const string FailedVariable = "nexusfailed";
        public const string MessageVariable = "nexusmsg";

        static readonly Regex Structure = new Regex(
            @"^(if|elseif|else|endif|do|enddo|break|skip|return|handle|elsehandle|endhandle)\b", RegexOptions.IgnoreCase);
        static readonly Regex HandleOpen = new Regex(@"^(handle|elsehandle)\b", RegexOptions.IgnoreCase);
        static readonly Regex HandleClose = new Regex(@"^endhandle\b", RegexOptions.IgnoreCase);

        public static List<string> Wrap(IList<string> lines)
        {
            var result = new List<string>
            {
                "!!" + FailedVariable + " = FALSE",
                "!!" + MessageVariable + " = ''",
            };

            int inUserHandler = 0;
            for (int i = 0; i < lines.Count; i++)
            {
                string line = lines[i];
                string trimmed = line.Trim();
                result.Add(line);

                if (HandleOpen.IsMatch(trimmed)) { inUserHandler++; continue; }
                if (HandleClose.IsMatch(trimmed)) { if (inUserHandler > 0) inUserHandler--; continue; }
                if (inUserHandler > 0 || Structure.IsMatch(trimmed)) continue;

                // the user protected this statement themselves
                if (i + 1 < lines.Count && HandleOpen.IsMatch(lines[i + 1].Trim())) continue;

                result.Add("handle any");
                result.Add("!!" + FailedVariable + " = TRUE");
                result.Add("!!" + MessageVariable + " = !!error.text");
                result.Add("return");
                result.Add("endhandle");
            }
            return result;
        }
    }
}
