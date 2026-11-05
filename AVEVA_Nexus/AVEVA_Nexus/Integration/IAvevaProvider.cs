using AVEVA_Nexus.Models;
using System.Collections.Generic;

namespace AVEVA_Nexus.Integration
{
    public interface IAvevaProvider
    {
        bool ExecuteCommand(string command);

        string GetStringVariable(string name);

        double GetRealVariable(string name);

        bool GetBooleanVariable(string name);

        object[] GetArrayVariable(string name);

        object GetAttribute(
            string dbref,
            string attribute);

        // dbrefs (e.g. "=123/45") of every element of <type> (full or short type name), optionally filtered by a PML condition and limited
        // to a scope (dbref or "/NAME"). Inputs must already be validated. Throws NexusException(400) on bad type/PML.
        string[] CollectRefs(string type, string scope, string where);

        // False for an invalid or non-existent dbref (E3D returns an element with IsValid == false, no exception).
        bool ElementExists(string dbref);

        // Member dbrefs of an existing element, optionally only of one type (404 if the element does not exist,
        // 400 if the type is unknown).
        string[] GetChildRefs(string dbref, string type);

        string GetOwnerRef(string dbref);

        // Dbref of the element a validated reference (dbref or "/NAME", see PmlInputValidator.ElementRef) points
        // to, or null if there is none. Resolved through the database API (no PML), so any name works.
        string ResolveRef(string reference);

        // Same for many references in one gate call; a null reference gives an entry with null Dbref/Type.
        ResolvedElement[] Resolve(IList<string> references);

        // Pre-order walk below an existing element (404 otherwise) down to <depth> levels, listing at most <limit>
        // items; <type> (full or short name, 400 if unknown) filters what is listed, not what is traversed.
        TreeResult GetTree(string dbref, int depth, string type, int limit);

        // System attributes then UDAs (e.g. ":TP-MTO-AREA") valid for an element type. 400 if the type is unknown.
        string[] GetAttributeNames(string type);

        // Runs each unit in order (a failing line or block is recorded and the rest still run), then reads the
        // requested global variables (names without "!!"). Plain lines run one by one; an if/do/handle block runs as
        // a whole. Caller must have checked read-only mode and validated the names.
        RunResult RunScript(IList<ScriptUnit> units, IList<string> returnVariables);

        // Project, MDB, user, module and version of the running session; anything unavailable is null.
        // (ReadOnly is filled in by the service.)
        SessionInfo GetSessionInfo();

        // Attribute values of one element as E3D text; unknown attribute or unreadable value -> null.
        Dictionary<string, object> ReadAttributes(string dbref, IList<string> attributes);
    }
}