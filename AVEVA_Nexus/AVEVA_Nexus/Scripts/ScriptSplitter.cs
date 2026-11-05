using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Models;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace AVEVA_Nexus.Scripts
{
    // Splits a PML script into units for PML/Run. Plain lines run one by one (so each failure is reported on its own);
    // an if/do/handle block must run as a whole, so all its lines form one unit.
    // A handle block protects only the statement right before it (verified on E3D 3.1), so a handle block that
    // follows a unit is joined to that unit: run on its own it would protect nothing.
    // Pure logic: must not reference Aveva.* (it is compiled by the unit-test runner).
    public static class ScriptSplitter
    {
        static readonly Regex Opener = new Regex(@"^(if|do|handle)\b", RegexOptions.IgnoreCase);
        static readonly Regex Closer = new Regex(@"^(endif|enddo|endhandle)\b", RegexOptions.IgnoreCase);
        static readonly Regex HandleOpener = new Regex(@"^handle\b", RegexOptions.IgnoreCase);

        public static List<ScriptUnit> Split(string script)
        {
            var units = new List<ScriptUnit>();
            ScriptUnit block = null;
            int depth = 0;
            int blockStart = 0;

            string[] raw = (script ?? "").Replace("\r", "").Split('\n');
            for (int i = 0; i < raw.Length; i++)
            {
                int number = i + 1;
                string line = raw[i].Trim();
                if (line.Length == 0 || line.StartsWith("--"))
                {
                    continue;
                }

                if (Opener.IsMatch(line))
                {
                    if (depth == 0)
                    {
                        block = new ScriptUnit { FirstLine = number, IsBlock = true };
                        blockStart = number;
                    }
                    depth++;
                }
                else if (Closer.IsMatch(line))
                {
                    if (depth == 0)
                    {
                        throw new NexusException("Unexpected '" + line + "' at line " + number + ": no open block", 400);
                    }
                    depth--;
                }

                if (depth > 0 || (block != null && Closer.IsMatch(line)))
                {
                    block.Lines.Add(line);
                    if (depth == 0)
                    {
                        block.LastLine = number;
                        ScriptUnit previous = units.Count > 0 ? units[units.Count - 1] : null;
                        if (previous != null && HandleOpener.IsMatch(block.Lines[0]))
                        {
                            previous.Lines.AddRange(block.Lines);
                            previous.LastLine = number;
                            previous.IsBlock = true;
                        }
                        else
                        {
                            units.Add(block);
                        }
                        block = null;
                    }
                }
                else
                {
                    units.Add(new ScriptUnit { FirstLine = number, LastLine = number, Lines = { line } });
                }
            }

            if (depth > 0)
            {
                throw new NexusException("The block opened at line " + blockStart + " is never closed", 400);
            }
            return units;
        }
    }
}
