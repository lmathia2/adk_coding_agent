# Implementation Rules

1. **Source priority:** Reuse existing code, then prefer the standard library,
   platform-native features, installed dependencies, and a one-line solution. Write
   new code only when those options do not hold.
2. **Dependency discipline:** Do not introduce a dependency for something a few clear,
   tested lines can handle.
3. **Guardrails:** Do not simplify away safety, trust-boundary validation, or error
   handling that prevents data loss.
4. **Debt annotations:** Mark deliberate corners with their ceiling and upgrade trigger:
   `# debt: <current ceiling>; upgrade when <measurable condition>`.
