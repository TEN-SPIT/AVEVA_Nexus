using AVEVA_Nexus.Exceptions;
using System;

namespace AVEVA_Nexus.Validation
{
    // Turns a client reference (dbref or "/NAME") into the dbref of an existing element. The resolver is the
    // provider's ResolveRef (database API; null when not found). Pure logic: no Aveva.* references.
    public static class ElementRefs
    {
        public static string Require(string value, Func<string, string> resolve)
        {
            string reference = PmlInputValidator.ElementRef(value);
            string dbref = resolve(reference);
            if (dbref == null)
            {
                throw new NexusException("Element " + reference + " not found", 404);
            }
            return dbref;
        }
    }
}
