using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Models;
using System;
using System.Collections.Generic;
using System.Linq;
using ac = Aveva.Core.Utilities.CommandLine;
using Aveva.Core.Database;

namespace AVEVA_Nexus.Integration
{
    public class AvevaProvider : IAvevaProvider
    {
        // Created lazily and only from code running inside AvevaGate: creating a Command is an E3D call, and
        // providers are built on every request (outside the lock) by the router's controllers.
        private ac.Command _getCommand;
        private ac.Command getCommand
        {
            get { return _getCommand ?? (_getCommand = ac.Command.CreateCommand("")); }
        }

        // Runs the lines as ONE temporary macro wrapped by PmlGuard, so a PML error is caught instead of opening
        // E3D's modal alert (which hung a graphical E3D). Returns false if a line failed; message is then the text
        // E3D gave for it (null if none could be read). Must be called inside AvevaGate.
        private bool RunGuarded(IList<string> lines, out string message)
        {
            message = null;
            string path = System.IO.Path.Combine(
                System.IO.Path.GetTempPath(), "nexus-" + Guid.NewGuid().ToString("N") + ".pmlmac");
            try
            {
                System.IO.File.WriteAllText(
                    path, string.Join("\r\n", Scripts.PmlGuard.Wrap(lines)), new System.Text.UTF8Encoding(false));
                bool ran = ac.Command.CreateCommand("$M/" + path.Replace('\\', '/')).RunInPdms();
                if (!ran)
                {
                    // the macro itself could not run (not a PML error inside it)
                    ac.Command.OutputAndClearError();
                    return false;
                }

                bool failed = false;
                try { failed = getCommand.GetPMLVariableBoolean(Scripts.PmlGuard.FailedVariable.ToUpper()); }
                catch (Exception) { }
                if (failed)
                {
                    try
                    {
                        string text = getCommand.GetPMLVariableString(Scripts.PmlGuard.MessageVariable.ToUpper());
                        if (!string.IsNullOrWhiteSpace(text)) message = text.Trim();
                    }
                    catch (Exception) { }
                }
                return !failed;
            }
            finally
            {
                try { System.IO.File.Delete(path); } catch (Exception) { }
            }
        }

        private static string WithMessage(string text, string message)
        {
            return message == null ? text : text + " [E3D: " + message + "]";
        }

        // Runs a PML command. A failure is reported with the command itself plus E3D's message when available.
        public bool ExecuteCommand(string command)
        {
            return AvevaGate.Run(() =>
            {
                try
                {
                    string message;
                    if (!RunGuarded(new[] { command }, out message))
                    {
                        throw new NexusException(WithMessage("PML command failed: " + command, message), 400);
                    }
                    return true;
                }
                catch (NexusException)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    // the stack trace goes to the log only, never into the response
                    Logging.NexusLogger.Error(ex);
                    throw new NexusException(ex.Message);
                }
            });
        }

        public string GetStringVariable(string name)
        {
            return AvevaGate.Run(() =>
            {
                RequireVariable(name);
                return Guarded(() => getCommand.GetPMLVariableString(name.ToUpper()));
            });
        }

        public double GetRealVariable(string name)
        {
            return AvevaGate.Run(() =>
            {
                RequireVariable(name);
                return Guarded(() => getCommand.GetPMLVariableReal(name.ToUpper()));
            });
        }

        public bool GetBooleanVariable(string name)
        {
            return AvevaGate.Run(() =>
            {
                RequireVariable(name);
                return Guarded(() => getCommand.GetPMLVariableBoolean(name.ToUpper()));
            });
        }

        // Elements come back in PML order (GetStringArrayFromPML needs the "!!" prefix; verified on E3D 3.1).
        public object[] GetArrayVariable(string name)
        {
            return AvevaGate.Run(() =>
            {
                RequireVariable(name);
                return Guarded(() => ac.Command.GetStringArrayFromPML("!!" + name).Cast<object>().ToArray());
            });
        }

        public object GetAttribute(
            string dbref,
            string attribute)
        {
            return AvevaGate.Run<object>(() =>
            {
                DbAttribute dbAttribute = DbAttribute.GetDbAttribute(attribute);
                DbElement dbElement = DbElement.GetElement(dbref);
                return dbElement.GetAsString(dbAttribute);
            });
        }

        // GetElementType("FOO") with an unknown name crashes the E3D session, so every type is mapped through the
        // list of real types first (cached; 1884 types on E3D 3.1). Clients may give the full name (EQUIPMENT) or
        // the short name (EQUI); the result is always the full name, or null if unknown. Called inside the gate.
        private static Query.TypeNameMap _typeMap;

        private static string CanonicalType(string type)
        {
            if (_typeMap == null)
            {
                _typeMap = new Query.TypeNameMap(DbElementType.GetAllElementTypes()
                    .Select(t => new KeyValuePair<string, string>(t.Name, ShortNameOf(t)))
                    .ToList());
            }
            return _typeMap.Canonical(type);
        }

        // NEEDS LIVE VERIFICATION: DbElementType.ShortName (not used elsewhere yet). Any failure -> no short name.
        private static string ShortNameOf(DbElementType type)
        {
            try { return type.ShortName; }
            catch (Exception) { return null; }
        }

        private static string RequireType(string type)
        {
            string canonical = CanonicalType(type);
            if (canonical == null)
            {
                throw new NexusException("Unknown element type '" + type + "'", 400);
            }
            return canonical;
        }

        // A validated reference (dbref "=123/45" or name "/NAME", see PmlInputValidator.ElementRef) to a valid
        // element, or null. Names go through the database API (MDB.FindElement), never into PML, so names with
        // quotes work. Called inside the gate.
        private static DbElement FindByRef(string reference)
        {
            if (string.IsNullOrEmpty(reference)) return null;
            try
            {
                DbElement element = reference.StartsWith("=")
                    ? DbElement.GetElement(reference)
                    : MDB.CurrentMDB.FindElement(reference);
                if ((element == null || !element.IsValid) && reference.StartsWith("/") && reference.Length > 1)
                {
                    // NEEDS LIVE VERIFICATION: in case FindElement expects the name without its leading '/'
                    element = MDB.CurrentMDB.FindElement(reference.Substring(1));
                }
                return element != null && element.IsValid ? element : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static string TypeOf(DbElement element)
        {
            try { return element.GetElementType().Name; }
            catch (Exception) { return null; }
        }

        private static string NameOf(DbElement element)
        {
            try { return element.GetAsString(DbAttribute.GetDbAttribute("NAME")); }
            catch (Exception) { return null; }
        }

        public string ResolveRef(string reference)
        {
            return AvevaGate.Run(() =>
            {
                DbElement element = FindByRef(reference);
                return element == null ? null : RefOf(element);
            });
        }

        public ResolvedElement[] Resolve(IList<string> references)
        {
            return AvevaGate.Run(() => references.Select(reference =>
            {
                DbElement element = FindByRef(reference);
                return new ResolvedElement
                {
                    Name = reference,
                    Dbref = element == null ? null : RefOf(element),
                    Type = element == null ? null : TypeOf(element)
                };
            }).ToArray());
        }

        // The whole walk runs in ONE gate call (one hop to the interface thread in graphical sessions).
        public TreeResult GetTree(string dbref, int depth, string type, int limit)
        {
            return AvevaGate.Run(() =>
            {
                DbElement root = RequireElement(dbref);
                string wanted = type == null ? null : RequireType(type);

                var result = new TreeResult
                {
                    Root = new TreeNode { Dbref = RefOf(root), Type = TypeOf(root), Name = NameOf(root) }
                };
                result.Truncated = Query.TreeWalk.Walk<DbElement>(
                    root,
                    element => element.Members(),
                    element => wanted == null || string.Equals(TypeOf(element), wanted, StringComparison.OrdinalIgnoreCase),
                    depth,
                    limit,
                    (element, owner, level) => result.Items.Add(new TreeItem
                    {
                        Dbref = RefOf(element),
                        Type = TypeOf(element),
                        Name = NameOf(element),
                        Depth = level,
                        Owner = RefOf(owner)
                    }));
                return result;
            });
        }

        // A scope is a dbref ("=123/45") or a name ("/SITE-NAME"). Called inside the gate.
        private static bool ScopeExists(string scope)
        {
            try
            {
                DbElement element = scope.StartsWith("=")
                    ? DbElement.GetElement(scope)
                    : MDB.CurrentMDB.FindElement(scope);
                return element != null && element.IsValid;
            }
            catch (Exception)
            {
                return false;
            }
        }

        public string[] CollectRefs(string type, string scope, string where)
        {
            return AvevaGate.Run(() =>
            {
                type = RequireType(type);

                // PML answers an unknown scope with an empty result, which callers read as "nothing here".
                if (scope != null && !ScopeExists(scope))
                {
                    throw new NexusException("Scope '" + scope + "' not found", 404);
                }

                string command = "var !!nexusQ collect all " + type
                    + (where != null ? " with (" + where + ")" : "")
                    + (scope != null ? " for " + scope : "");

                string collectMessage;
                if (!RunGuarded(new[] { command }, out collectMessage))
                {
                    throw new NexusException(WithMessage(
                        "PML collect failed (check the where condition and scope): " + command, collectMessage), 400);
                }

                string[] refs = ac.Command.GetStringArrayFromPML("!!nexusQ");

                // best-effort cleanup of the temporary global
                if (!ac.Command.CreateCommand("!!nexusQ.delete()").RunInPdms())
                {
                    ac.Command.OutputAndClearError();
                }
                return refs;
            });
        }

        public RunResult RunScript(IList<ScriptUnit> units, IList<string> returnVariables)
        {
            return AvevaGate.Run(() =>
            {
                var result = new RunResult();
                foreach (ScriptUnit unit in units)
                {
                    string message;
                    if (!RunGuarded(unit.Lines, out message))
                    {
                        result.Errors.Add(WithMessage(unit.IsBlock
                            ? "PML block failed (lines " + unit.FirstLine + "-" + unit.LastLine + "): " + unit.Lines[0]
                            : "PML command failed: " + unit.Lines[0], message));
                    }
                }
                foreach (string name in returnVariables)
                {
                    result.Values[name] = ReadGlobal(name);
                }
                result.Ok = result.Errors.Count == 0;
                return result;
            });
        }

        // Typed read of a global variable: arrays as string[], REAL as double, BOOLEAN as bool, the rest as text.
        // Undefined (or unreadable) variables give null.
        private object ReadGlobal(string name)
        {
            if (!VariableExists(name))
            {
                return null;
            }

            string kind = QueryText("!!nexusKind = !!" + name + ".objectType()", "NEXUSKIND");
            if (kind == "ARRAY")
            {
                return ac.Command.GetStringArrayFromPML("!!" + name);
            }

            string text = QueryText("!!nexusText = !!" + name + ".string()", "NEXUSTEXT");
            if (text == null)
            {
                return null;
            }
            switch (kind)
            {
                case "REAL":
                    double number;
                    return double.TryParse(text, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out number) ? (object)number : text;
                case "BOOLEAN":
                    return string.Equals(text, "TRUE", StringComparison.OrdinalIgnoreCase);
                default:
                    return text;
            }
        }

        private string QueryText(string command, string variable)
        {
            if (!ac.Command.CreateCommand(command).RunInPdms())
            {
                ac.Command.OutputAndClearError();
                return null;
            }
            return getCommand.GetPMLVariableString(variable);
        }

        // Module has no PML/API accessor; the TTY module process runs with "-modulename=Design", so it is read from
        // the command line. Version comes from the running executable. Each value is null if unavailable.
        public SessionInfo GetSessionInfo()
        {
            return AvevaGate.Run(() => new SessionInfo
            {
                Project = Safe(() => Project.CurrentProject.Code),
                Mdb = Safe(() => MDB.CurrentMDB.Name),
                User = Safe(() => Project.CurrentProject.UserName),
                Module = Safe(() =>
                {
                    var match = System.Text.RegularExpressions.Regex.Match(
                        Environment.CommandLine, @"-modulename=(\S+)", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                    return match.Success ? match.Groups[1].Value : null;
                }),
                E3dVersion = Safe(() => System.Diagnostics.Process.GetCurrentProcess().MainModule.FileVersionInfo.ProductVersion)
            });
        }

        private static string Safe(Func<string> read)
        {
            try { return read(); }
            catch (Exception) { return null; }
        }

        public bool ElementExists(string dbref)
        {
            return AvevaGate.Run(() => DbElement.GetElement(dbref).IsValid);
        }

        private static DbElement RequireElement(string dbref)
        {
            DbElement element = DbElement.GetElement(dbref);
            if (!element.IsValid)
            {
                throw new NexusException("Element " + dbref + " not found", 404);
            }
            return element;
        }

        private static string RefOf(DbElement element)
        {
            return element.GetAsString(DbAttribute.GetDbAttribute("REF"));
        }

        public string[] GetChildRefs(string dbref, string type)
        {
            return AvevaGate.Run(() =>
            {
                DbElement element = RequireElement(dbref);
                if (type != null) type = RequireType(type);

                var refs = new List<string>();
                foreach (DbElement member in element.Members())
                {
                    if (type != null && !string.Equals(member.GetElementType().Name, type, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                    refs.Add(RefOf(member));
                }
                return refs.ToArray();
            });
        }

        public string GetOwnerRef(string dbref)
        {
            return AvevaGate.Run(() =>
            {
                DbElement element = RequireElement(dbref);
                // WORLD has no owner: Owner is null or invalid, and reading REF of it throws (was a 500).
                try
                {
                    DbElement owner = element.Owner;
                    return owner == null || !owner.IsValid ? null : RefOf(owner);
                }
                catch (Exception)
                {
                    return null;
                }
            });
        }

        public string[] GetAttributeNames(string type)
        {
            return AvevaGate.Run(() =>
            {
                DbElementType elementType = DbElementType.GetElementType(RequireType(type));
                return elementType.SystemAttributes()
                    .Concat(elementType.GetAllUdas())
                    .Select(a => a.Name)
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToArray();
            });
        }

        public Dictionary<string, object> ReadAttributes(string dbref, IList<string> attributes)
        {
            return AvevaGate.Run(() =>
            {
                var values = new Dictionary<string, object>();
                DbElement element = DbElement.GetElement(dbref);
                foreach (string name in attributes)
                {
                    object value = null;
                    try
                    {
                        DbAttribute attribute = DbAttribute.GetDbAttribute(name);
                        if (attribute != null && element.IsValid)
                        {
                            value = element.GetAsString(attribute);
                        }
                    }
                    catch (Exception)
                    {
                        value = null;
                    }
                    values[name] = value;
                }
                return values;
            });
        }

        private void RequireVariable(string name)
        {
            if (!VariableExists(name))
            {
                throw new VariableNotFoundException(name);
            }
        }

        private static T Guarded<T>(Func<T> read)
        {
            try
            {
                return read();
            }
            catch (Exception ex)
            {
                Logging.NexusLogger.Error(ex);
                throw new NexusException(ex.Message);
            }
        }

        private bool VariableExists(string name)
        {
            try
            {
                getCommand.CommandString =
                    $"!!varExist = object boolean()";
                getCommand.RunInPdms();

                getCommand.CommandString =
                    $"!!varExist = defined( !!{name} )";
                getCommand.RunInPdms();

                return getCommand.GetPMLVariableBoolean("VAREXIST");
            }
            catch
            {
                return false;
            }
        }
    }
}
