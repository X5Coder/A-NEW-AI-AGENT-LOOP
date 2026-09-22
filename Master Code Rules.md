# Code Rules

## 1. Purpose

This document defines the mandatory rules for writing clean, correct, maintainable, secure, reliable, and professional software.

These rules are language-independent and apply to any programming language, framework, library, architecture, or project.

The goal is to produce software that is:

* Correct
* Simple
* Readable
* Maintainable
* Testable
* Secure
* Reliable
* Efficient
* Predictable
* Extensible

These rules are mandatory.

Do not introduce unnecessary complexity.

When rules conflict, prioritize:

1. Correctness
2. Security
3. Data integrity
4. Explicit requirements
5. Maintainability
6. Performance
7. Convenience

---

# 2. Core Principles

## 2.1 Correctness First

Correct behavior is more important than:

* Shorter code
* Fewer files
* Fewer lines
* Clever implementations
* Premature optimization
* Architectural complexity

Never knowingly introduce incorrect behavior to make implementation easier.

---

## 2.2 Simplicity First

Always prefer the simplest solution that correctly solves the actual problem.

Do not add:

* Unnecessary abstractions
* Unnecessary classes
* Unnecessary interfaces
* Unnecessary layers
* Unnecessary wrappers
* Unnecessary managers
* Unnecessary factories
* Unnecessary dependencies
* Unnecessary configuration
* Unnecessary indirection

Professional code is not code with the most abstraction.

---

## 2.3 Readability First

Code must be understandable by another developer without requiring the original author to explain it.

Prefer:

* Clear names
* Simple control flow
* Focused functions
* Explicit behavior
* Predictable data flow
* Consistent patterns

Avoid clever, obscure, or unnecessarily compact implementations.

---

## 2.4 Single Responsibility

Every component should have one primary responsibility.

A function, class, module, or component should have a clear reason to change.

Do not combine unrelated responsibilities merely because they are convenient to place together.

---

## 2.5 High Cohesion

Related behavior should stay together.

A component should contain functionality that naturally belongs to the same responsibility.

Do not scatter one feature across unrelated parts of the system without a real reason.

---

## 2.6 Low Coupling

Components should depend on as little unrelated functionality as reasonably possible.

Changing one component should not unnecessarily require changes to unrelated components.

Avoid:

* Circular dependencies
* Hidden dependencies
* Unnecessary shared state
* Unnecessary global state
* Tight coupling
* Uncontrolled cross-component mutation

---

# 3. Separation of Concerns

Different responsibilities should remain separated.

Examples include:

* Business logic
* User interface
* Persistence
* Networking
* Configuration
* External services
* Validation
* File handling
* Logging

Do not mix unrelated concerns merely to reduce the number of files.

Separation should be based on actual responsibility, not arbitrary architectural rules.

---

# 4. Architecture

Architecture must serve the requirements.

Do not create architecture for its own sake.

Avoid adding layers simply because:

* A design pattern recommends them
* Another project uses them
* They appear more professional
* They may be useful someday

Use an architectural component when it provides a real benefit.

Architecture should remain:

* Understandable
* Consistent
* Proportional to project complexity
* Easy to modify
* Easy to test

---

# 5. Avoid Over-Engineering

Do not build features before they are required.

Avoid speculative:

* Abstractions
* Interfaces
* Extension systems
* Plugin systems
* Configuration systems
* Factories
* Frameworks
* Generic utilities

Build what is required now while keeping the implementation clean enough to evolve later.

---

# 6. Data Structures

Choose data structures according to actual requirements and access patterns.

Consider:

* Ordering
* Uniqueness
* Lookup frequency
* Insertion frequency
* Removal frequency
* Mutation
* Memory usage
* Data size
* Concurrency
* Persistence requirements

Do not choose a data structure simply because it is familiar.

---

## 6.1 Sequences

Use sequences when:

* Order matters
* Duplicate values are valid
* Sequential processing is required

Do not use a sequence for frequent membership checks when a more appropriate structure exists.

---

## 6.2 Sets

Use set-like structures when:

* Uniqueness matters
* Membership checks are frequent
* Ordering is not the primary requirement

Do not use them when deterministic ordering is required unless the chosen implementation guarantees it.

---

## 6.3 Maps / Dictionaries

Use map-like structures when:

* Data naturally has keys
* Direct lookup is required
* Key-value relationships are meaningful

Avoid unnecessarily deep nested maps.

When the structure becomes difficult to understand, use a clearer domain representation.

---

## 6.4 Queues

Use queues for ordered work such as:

* Tasks
* Messages
* Events
* Background jobs

Queues should have appropriate limits when unbounded growth is possible.

---

## 6.5 Stacks

Use stack-like structures when LIFO behavior is actually required.

Do not simulate stack behavior through unnecessarily complicated structures.

---

## 6.6 Trees and Graphs

Use trees or graphs when relationships are inherently:

* Hierarchical
* Parent-child
* Networked
* Many-to-many
* Traversal-oriented

Do not force naturally relational data into flat structures if doing so creates unnecessary complexity.

---

## 6.7 Custom Data Structures

Create custom data structures only when built-in structures cannot clearly or efficiently represent the required behavior.

Do not create custom abstractions for trivial data.

---

# 7. Data Ownership

Every important piece of mutable data should have a clear owner.

Always be able to answer:

> Who owns this data?

Avoid multiple unrelated components freely modifying the same state.

Prefer controlled ownership and explicit mutation.

---

# 8. Immutability and Mutation

Prefer immutable data where practical.

Mutation must be deliberate.

Avoid unexpected mutation of:

* Shared objects
* Function arguments
* Global state
* Cached data
* Configuration
* State owned by another component

When mutable state is necessary:

* Keep ownership clear
* Limit its scope
* Control access
* Preserve important invariants

---

# 9. State Management

State must have:

* A clear owner
* A clear lifecycle
* A clear source of truth

Avoid multiple independent copies of the same important state.

Do not create multiple sources of truth unless synchronization is explicitly designed.

When state becomes complex, model it explicitly instead of hiding complexity inside boolean variables and scattered conditions.

---

# 10. Input Validation

All external input is untrusted.

External input includes:

* User input
* Network requests
* API responses
* Files
* Database content
* Configuration
* Third-party services
* Model-generated data
* Serialized data

Validate at system boundaries.

Validate:

* Type
* Shape
* Required fields
* Allowed values
* Size
* Range
* Encoding
* Permissions
* Security constraints

Never assume external data is valid.

---

# 11. Null, Empty, Missing, and Invalid Data

Do not automatically treat these states as equivalent:

* Missing
* Null
* Empty
* Zero
* False
* Invalid

Handle each according to the actual requirements.

Avoid ambiguous APIs where possible.

---

# 12. Serialization and Deserialization

Never blindly trust serialized data.

When deserializing:

* Validate the structure
* Validate types
* Validate required fields
* Handle missing fields
* Handle unexpected fields appropriately
* Reject malformed data safely

Never execute arbitrary serialized content.

---

# 13. Functions

Functions should perform one coherent operation.

Prefer functions that are:

* Focused
* Predictable
* Small enough to understand
* Easy to test

Avoid:

* Huge functions
* Deep nesting
* Excessive branching
* Hidden side effects
* Excessive parameters
* Unclear return values

Do not split functions into meaningless micro-functions simply to reduce line count.

The goal is meaningful responsibility.

---

# 14. Function Parameters

Keep parameters understandable.

Avoid functions requiring excessive unrelated parameters.

If multiple values naturally belong together, use an appropriate data structure.

Do not use long parameter lists as a substitute for proper design.

---

# 15. Return Values

Return predictable results.

A function should have a clear contract.

Avoid returning completely different structures depending on hidden conditions.

Represent success and failure clearly.

---

# 16. Classes and Components

Use classes or components when they provide real value.

Good reasons include:

* Encapsulating state
* Representing a meaningful domain concept
* Managing a lifecycle
* Providing a coherent responsibility
* Supporting multiple genuine implementations

Do not create classes simply because the language supports classes.

Avoid:

* Empty wrappers
* Pass-through classes
* God classes
* Generic managers
* Classes containing unrelated responsibilities

---

# 17. Naming

Names must communicate intent.

Prefer:

* Specific names
* Descriptive names
* Consistent terminology
* Domain-appropriate terminology

Avoid meaningless names such as:

```text
thing
stuff
data
misc
helper
manager
processor
handler
temp
foo
bar
```

unless their meaning is genuinely clear in context.

Avoid unnecessary abbreviations.

Use the same terminology consistently throughout the project.

---

# 18. Boolean and Conditional Logic

Keep conditional logic easy to understand.

Avoid deeply nested conditions.

Avoid unnecessarily complex boolean expressions.

When a condition is important or complex, give it a meaningful name.

If the number of states becomes difficult to reason about, model the states explicitly.

---

# 19. Control Flow

Prefer straightforward control flow.

Avoid:

* Excessive nesting
* Clever one-line logic
* Deep chains of conditions
* Hidden execution paths
* Unnecessary recursion
* Complex control flow without a clear benefit

The execution path should be easy to follow.

---

# 20. Duplication

Avoid unnecessary duplication.

However, do not create an abstraction merely because two pieces of code currently look similar.

Prefer duplication over a bad abstraction when the responsibilities are actually different.

Extract shared logic when the behavior genuinely belongs together.

---

# 21. Abstraction

Create abstractions only when there is a real reason.

Good reasons include:

* Multiple genuine implementations
* Reusable behavior
* Stable boundaries
* External system isolation
* Testability
* Real extensibility requirements

Do not create speculative abstractions for hypothetical future requirements.

---

# 22. Comments

Comments should explain **why**, not simply repeat **what** the code does.

Useful comments explain:

* Non-obvious decisions
* Important constraints
* Workarounds
* External limitations
* Security considerations
* Reasons behind unusual behavior

Avoid comments that merely translate code into English.

If code requires many comments to explain basic behavior, improve the code.

---

# 23. Magic Values

Avoid unexplained constants.

Meaningful values should have descriptive names or appropriate configuration.

Do not scatter the same unexplained value throughout the codebase.

---

# 24. Side Effects

Side effects must be intentional and understandable.

Examples:

* Database writes
* File writes
* Network requests
* Sending messages
* External API calls
* Process creation
* State mutation

Do not hide significant side effects inside functions whose names imply pure calculation or validation.

---

# 25. Global State

Avoid global mutable state.

Global mutable state makes:

* Testing harder
* Dependencies unclear
* Concurrency harder
* Debugging harder
* Behavior less predictable

If global state is genuinely necessary, isolate it and control access carefully.

---

# 26. Memory Management

Memory usage must be controlled.

Never allow data structures to grow indefinitely without a deliberate reason.

Pay special attention to:

* Large collections
* Caches
* Queues
* Buffers
* Logs
* Event lists
* Conversation history
* Temporary data
* Network responses
* File contents
* Background tasks

---

# 27. Memory Leaks

Prevent unintended retention of objects and resources.

Common causes include:

* Unbounded collections
* Unbounded caches
* Event listeners that are never removed
* Timers that remain active
* Background tasks that never terminate
* Long-lived references to temporary data
* Unclosed resources
* Repeated initialization
* Circular ownership
* Persistent references to large objects

Every long-lived object must have a justified lifetime.

---

# 28. Object Lifetime

For every long-lived object, understand:

1. Who creates it?
2. Who owns it?
3. Who uses it?
4. How long does it live?
5. When is it released?
6. What happens if initialization fails?

Prefer explicit lifecycles:

```text
create
→ initialize
→ use
→ release
```

Do not retain large objects longer than necessary.

---

# 29. Resource Management

Every acquired resource requires a cleanup strategy.

Resources include:

* Files
* Database connections
* Network connections
* Sockets
* Streams
* Processes
* Threads
* Background tasks
* Timers
* Event subscriptions
* Temporary files
* External handles

Always know:

* Who creates it
* Who owns it
* Who closes/releases it
* What happens when an error occurs

Never depend on luck or garbage collection for important resource cleanup.

---

# 30. File Handling

Handle files safely.

Consider:

* Missing files
* Invalid files
* Permission errors
* Encoding errors
* Corrupted data
* Unexpected sizes
* Partial reads
* Concurrent access

Do not load very large files entirely into memory when streaming or chunking is more appropriate.

Clean up temporary files.

Never blindly trust user-provided filenames or paths.

---

# 31. Large Data

Large data must be handled intentionally.

Avoid:

* Loading entire datasets unnecessarily
* Repeatedly copying large structures
* Duplicating large strings
* Keeping huge results alive
* Passing massive objects between components
* Unbounded buffers

Prefer, when appropriate:

* Streaming
* Pagination
* Chunking
* Iteration
* Lazy loading
* Bounded buffers
* References or identifiers

---

# 32. Avoid Unnecessary Copies

Understand whether an operation:

* Copies data
* Mutates data
* References data
* Serializes data
* Deserializes data

Avoid unnecessary copies, especially for large data.

Repeated copying can cause both memory and performance problems.

---

# 33. Caching

Every cache must have a clear purpose.

A cache should define, when applicable:

* What is cached
* Why it is cached
* Maximum size
* Expiration
* Invalidation
* Ownership

Never create an unbounded cache without a deliberate reason.

Do not cache data that can become dangerously stale without an appropriate strategy.

---

# 34. Database Rules

Database operations must be intentional.

Avoid:

* Unbounded queries
* Loading entire tables unnecessarily
* N+1 query patterns
* Repeated identical queries
* Unnecessary writes
* Duplicate sources of truth

Use appropriate:

* Indexes
* Constraints
* Pagination
* Limits
* Transactions

when required.

---

# 35. Transactions

Use transactions when multiple changes must succeed or fail together.

Avoid partially updated state when atomicity is required.

Keep transactions as short as practical.

Do not hold a transaction open while performing unnecessary external operations.

---

# 36. Vector and Search Data

When using embeddings or semantic search:

* Preserve the original source data.
* Preserve source identity.
* Preserve useful metadata.
* Know which embedding representation was used.
* Make re-indexing possible when necessary.

An embedding is not a replacement for the original information.

Search results should be bounded and relevant.

Do not retrieve unlimited results.

---

# 37. Concurrency

Concurrency must be intentional.

Do not introduce concurrency merely because it appears faster.

Before using concurrent execution, consider:

* Shared state
* Race conditions
* Ordering
* Thread safety
* Cancellation
* Failure behavior
* Resource ownership
* Duplicate execution

---

# 38. Race Conditions

Protect shared mutable state.

Avoid:

* Check-then-act races
* Lost updates
* Double writes
* Duplicate execution
* Concurrent modification
* Unsynchronized shared state

Never assume execution order when concurrency is possible.

---

# 39. Asynchronous Work

Every asynchronous or background operation must have:

* A purpose
* A lifecycle
* Error handling
* Cancellation behavior
* Cleanup

Do not create background work that can run indefinitely without a legitimate reason.

Do not silently discard asynchronous failures.

---

# 40. Cancellation

Long-running operations should support cancellation when the environment requires it.

Cancellation should:

* Stop unnecessary work
* Release resources
* Leave state consistent
* Avoid orphaned tasks

Do not treat cancellation as an exceptional situation that can be ignored.

---

# 41. Idempotency

Operations that may be retried should be designed to avoid unintended duplicate effects when possible.

This is particularly important for:

* Network requests
* Message sending
* Database writes
* File operations
* External APIs
* Background jobs

Before retrying an operation, determine whether repeating it is safe.

---

# 42. Error Handling

Never silently ignore errors.

Every error should be:

* Handled
* Propagated
* Transformed into a meaningful error
* Or intentionally ignored for a documented reason

Do not catch errors merely to prevent crashes.

Do not hide programming bugs.

---

# 43. Error Messages

Errors should provide useful context without exposing sensitive information.

Where appropriate, include:

* What operation failed
* Relevant non-sensitive context
* Why it failed
* What component encountered the failure

Never expose:

* Passwords
* API keys
* Tokens
* Private credentials
* Sensitive personal data

---

# 44. Retry Policy

Retries must always be bounded.

Maximum retries:

**3 retries.**

Never retry indefinitely.

Only retry errors that are reasonably recoverable.

Do not blindly retry:

* Invalid input
* Permanent authorization failures
* Deterministic programming errors
* Permanent permission failures

unless there is a specific reason.

---

# 45. Timeouts

External operations should have appropriate timeouts when the environment supports them.

Never allow an unreliable external operation to block the entire system indefinitely without a deliberate reason.

---

# 46. Network Operations

Network operations are inherently unreliable.

Handle:

* Connection failures
* Timeouts
* Rate limits
* Invalid responses
* Partial responses
* Authentication failures
* Service outages
* Retries
* Cancellation

Never assume the network is always available.

---

# 47. External APIs

Keep third-party API-specific behavior isolated.

Do not spread API-specific assumptions throughout unrelated code.

Validate external responses.

Do not assume third-party services always behave correctly.

Expect API changes and failures.

---

# 48. Security

Security is part of correctness.

Treat all external input as untrusted.

Never blindly trust:

* User input
* Files
* Network responses
* Third-party data
* Model output
* Serialized objects
* External configuration

Validate and constrain all of them.

---

# 49. Secrets

Never hardcode:

* Passwords
* API keys
* Tokens
* Private keys
* Credentials

Never commit real secrets to source control.

Never log secrets.

Never expose secrets through errors, responses, or debugging output.

Use appropriate environment or secret-management mechanisms.

---

# 50. File Path Security

Never blindly trust user-controlled paths.

Protect against:

* Path traversal
* Unauthorized file access
* Writing outside permitted directories
* Reading arbitrary files
* Dangerous symbolic links where applicable

Resolve and validate paths against allowed locations.

---

# 51. Code Execution

Never execute user-provided or model-generated code/commands blindly.

Any capability that can execute code, commands, scripts, or system operations must have explicit validation and appropriate security boundaries.

Do not assume that generated output is safe merely because it came from an AI model.

---

# 52. Model-Generated Data

AI-generated output is untrusted data.

Validate model-generated:

* Tool arguments
* File paths
* Commands
* URLs
* Queries
* Structured data
* Configuration
* Identifiers

before performing consequential operations.

---

# 53. Logging

Logs should be useful without becoming a data leak.

Do not log:

* Passwords
* Tokens
* API keys
* Private credentials
* Sensitive personal information
* Entire private datasets unnecessarily

Avoid excessive logging.

Log meaningful:

* Failures
* Important state changes
* Lifecycle events
* Operational information
* Debugging information when appropriate

---

# 54. Privacy

Collect and retain only data that is actually required.

Do not store sensitive information unnecessarily.

Do not duplicate private data without a reason.

Keep access to private data limited to components that genuinely require it.

---

# 55. Dependencies

Do not add dependencies without a real reason.

Before adding a dependency, ask:

* Is it actually necessary?
* Can existing functionality solve the problem?
* Does it increase complexity?
* Is it maintained?
* Does it introduce security risks?
* Does it introduce licensing concerns?
* Does it create unnecessary coupling?

Prefer fewer dependencies when practical.

---

# 56. Performance

Do not prematurely optimize.

First make the code:

1. Correct
2. Simple
3. Readable
4. Maintainable

Optimize when:

* There is a measured problem
* There is a known requirement
* The algorithm is clearly inappropriate
* Resource usage is predictably excessive

Do not sacrifice readability for theoretical performance gains.

---

# 57. Algorithmic Complexity

Consider time and space complexity when processing significant amounts of data.

Avoid unnecessary:

* Repeated full scans
* Nested loops over large datasets
* Network requests
* Database queries
* Serialization
* Deserialization
* File reads
* Model loading

Use appropriate indexes, batching, caching, or data structures when justified.

---

# 58. Performance vs Memory

Do not optimize CPU usage by creating uncontrolled memory growth.

Do not optimize memory usage by creating excessive repeated computation without reason.

Consider both:

* Time
* Space

when designing important operations.

---

# 59. Testing

Tests must verify real behavior.

Tests should be:

* Focused
* Deterministic
* Independent
* Readable
* Maintainable

Test:

* Normal behavior
* Edge cases
* Invalid input
* Failure paths
* Security boundaries
* Resource cleanup
* Retry behavior
* Concurrency behavior where applicable

---

# 60. Test Isolation

Tests should not depend on:

* Another test's execution order
* Shared mutable state
* Real private user data
* Uncontrolled external services
* Permanent local state

Clean up test resources.

Tests should be repeatable.

---

# 61. Error and Edge-Case Testing

Test failures, not only success.

Important cases include:

* Missing data
* Invalid data
* Empty data
* Very large data
* Network failure
* Timeout
* Permission failure
* Corrupt files
* Invalid external responses
* Retry exhaustion
* Cancellation
* Resource cleanup
* Concurrent access

---

# 62. Change Management

Before modifying existing code:

1. Understand the requirement.
2. Inspect the existing implementation.
3. Identify the correct responsibility.
4. Search for existing functionality.
5. Reuse existing behavior when appropriate.
6. Determine the smallest clean change.
7. Implement the change.
8. Test affected behavior.
9. Check for unintended side effects.
10. Update documentation when necessary.

---

# 63. Minimal Change Principle

When modifying existing code:

> Change only what needs to change.

Do not unnecessarily:

* Reformat unrelated code
* Rename unrelated components
* Move unrelated files
* Replace working implementations
* Introduce new architecture
* Change public behavior

Preserve existing correct behavior.

---

# 64. Refactoring

Refactoring should improve internal quality without intentionally changing behavior.

Good reasons to refactor include:

* Removing duplication
* Reducing coupling
* Improving readability
* Simplifying logic
* Improving testability
* Separating responsibilities
* Removing unnecessary complexity
* Fixing architectural problems

Do not refactor unrelated code during a feature change unless necessary.

---

# 65. No God Components

Never create a component that handles everything.

Avoid giant:

* Managers
* Controllers
* Handlers
* Services
* Utilities
* Processors
* Modules

When a component becomes too large, divide it according to genuine responsibilities.

---

# 66. No Dumping-Ground Code

Avoid generic locations for unrelated code.

Do not use vague concepts such as:

```text
misc
stuff
random
helpers
utils
common
temp
```

unless the responsibility is genuinely clear and justified.

---

# 67. No Hidden Magic

Avoid:

* Hidden side effects
* Unexpected mutation
* Implicit global state
* Automatic behavior that is difficult to trace
* Surprising initialization
* Invisible dependencies

Important behavior should be explicit.

---

# 68. Configuration

Separate configuration from implementation.

Do not scatter configuration values throughout source code.

Keep appropriate separation between:

* Source code
* Configuration
* Secrets
* Runtime data
* User data

Do not hardcode environment-specific values unnecessarily.

---

# 69. Runtime Data

Keep runtime data separate from source code.

Runtime data may include:

* Databases
* Caches
* Uploaded files
* Generated files
* Logs
* Temporary data
* Local models
* Runtime state

Do not treat runtime data as application source.

---

# 70. Documentation

Documentation must describe actual behavior.

Never document features that do not exist.

When behavior changes, update relevant documentation.

Documentation should explain important:

* Usage
* Configuration
* Constraints
* Architecture decisions
* Public interfaces
* Operational requirements

Do not document trivial implementation details unnecessarily.

---

# 71. Consistency

Use consistent conventions throughout the project.

Consistency applies to:

* Naming
* Formatting
* Error handling
* Logging
* Data structures
* API behavior
* File organization
* Testing
* Configuration

Do not introduce a different style without a real reason.

---

# 72. Compatibility

When modifying existing systems, consider:

* Existing users
* Existing data
* Existing interfaces
* Existing configuration
* Existing integrations

Do not break compatibility accidentally.

If a breaking change is necessary, make it explicit and handle migration appropriately.

---

# 73. Backward Compatibility

Before changing an existing public behavior, determine:

* Who depends on it?
* What data depends on it?
* What interfaces depend on it?
* Can migration be performed safely?

Do not assume that internal-looking behavior is unused.

---

# 74. Resource Limits

Any operation that can receive untrusted or large input should have appropriate limits where practical.

Examples:

* Maximum file size
* Maximum request size
* Maximum result count
* Maximum queue size
* Maximum memory usage
* Maximum execution time
* Maximum context size
* Maximum retry count

Limits prevent uncontrolled resource consumption.

---

# 75. Defensive Programming

Defend against realistic failure conditions.

Do not add pointless checks everywhere.

Use defensive programming where:

* Data is external
* State can become invalid
* Resources can fail
* Concurrency exists
* External systems are unreliable
* Security is involved

Do not replace clear contracts with endless defensive checks.

---

# 76. Fail Clearly

When the system cannot safely continue, fail in a clear and controlled manner.

Do not silently continue with corrupted or invalid state.

Do not hide the root cause behind meaningless generic errors.

---

# 77. Preserve Data Integrity

Never sacrifice data integrity for convenience.

When modifying persistent data:

* Validate before writing
* Use atomic operations where appropriate
* Use transactions when necessary
* Handle partial failures
* Avoid accidental overwrites
* Preserve backups or recovery mechanisms when required

---

# 78. Avoid Premature Generalization

Do not design for ten future use cases when only one is currently required.

Implement the current requirement cleanly.

Generalize when actual repetition or requirements justify it.

---

# 79. Avoid Clever Code

Do not optimize for showing technical sophistication.

Avoid code that requires the reader to mentally simulate complicated behavior.

Prefer:

```text
clear and obvious
```

over:

```text
short but clever
```

---

# 80. Prefer Explicitness

Important behavior should be visible.

Prefer explicit:

* Dependencies
* Inputs
* Outputs
* State changes
* Side effects
* Error behavior
* Resource ownership

Avoid relying on hidden conventions for critical behavior.

---

# 81. Before Writing Code

Before implementing a feature, ask:

1. What exactly is required?
2. What component owns this responsibility?
3. Does this functionality already exist?
4. Can existing code be reused?
5. What data does this require?
6. What resources does it acquire?
7. What can fail?
8. What external input is untrusted?
9. Can the operation grow without limits?
10. Does it introduce unnecessary coupling?
11. Does it require concurrency?
12. How will it be tested?
13. What is the simplest correct implementation?

---

# 82. Before Finishing Code

Before considering the implementation complete, verify:

### Code Quality

* [ ] Names are clear.
* [ ] Responsibilities are focused.
* [ ] Functions are understandable.
* [ ] Control flow is readable.
* [ ] No unnecessary abstraction exists.
* [ ] No unnecessary duplication exists.
* [ ] No giant component exists.
* [ ] No hidden behavior exists.

### Data

* [ ] Appropriate data structures are used.
* [ ] Data ownership is clear.
* [ ] Mutation is intentional.
* [ ] External data is validated.
* [ ] Missing/null/empty states are handled.
* [ ] Serialization is validated.

### Memory

* [ ] No unbounded collection exists.
* [ ] No unbounded cache exists.
* [ ] Large objects are not unnecessarily retained.
* [ ] Large data is not unnecessarily copied.
* [ ] Buffers and queues are controlled.
* [ ] Event listeners are cleaned up.
* [ ] Background tasks have a lifecycle.

### Resources

* [ ] Files are closed.
* [ ] Connections are released.
* [ ] Temporary resources are cleaned up.
* [ ] Background work can terminate.
* [ ] Failure paths also perform cleanup.

### Security

* [ ] External input is treated as untrusted.
* [ ] Secrets are protected.
* [ ] Paths are validated.
* [ ] Model output is validated.
* [ ] Dangerous operations are restricted.
* [ ] Sensitive data is not logged.

### Reliability

* [ ] Errors are handled.
* [ ] Timeouts exist where appropriate.
* [ ] Retries are bounded to 3.
* [ ] Retry behavior is safe.
* [ ] Partial failures are considered.
* [ ] Cancellation is handled where applicable.

### Performance

* [ ] No unnecessary expensive operations exist.
* [ ] No unnecessary network requests exist.
* [ ] No unnecessary database queries exist.
* [ ] Large data is handled efficiently.
* [ ] Memory usage is controlled.

### Testing

* [ ] Main success paths are tested.
* [ ] Failure paths are tested.
* [ ] Edge cases are considered.
* [ ] Tests are isolated.
* [ ] Tests are deterministic.

---

# 83. Absolute Rules

The following rules are mandatory:

1. **Correctness comes first.**
2. **Keep solutions as simple as possible.**
3. **Do not over-engineer.**
4. **Do not introduce architecture without a real requirement.**
5. **Every component must have a clear responsibility.**
6. **Keep related behavior together.**
7. **Keep unrelated behavior separated.**
8. **Minimize unnecessary coupling.**
9. **Avoid global mutable state.**
10. **Choose data structures based on actual requirements.**
11. **Make data ownership clear.**
12. **Validate external input.**
13. **Never blindly trust external or AI-generated data.**
14. **Do not allow uncontrolled memory growth.**
15. **Do not allow uncontrolled cache growth.**
16. **Do not allow uncontrolled queue growth.**
17. **Release resources correctly.**
18. **Handle failure paths and cleanup paths.**
19. **Never retry indefinitely.**
20. **Maximum retries are 3.**
21. **Never hardcode secrets.**
22. **Never log secrets.**
23. **Protect user data.**
24. **Do not blindly trust file paths.**
25. **Do not blindly execute generated code or commands.**
26. **Do not unnecessarily copy large data.**
27. **Do not load large data into memory unnecessarily.**
28. **Avoid unnecessary network and database operations.**
29. **Do not hide important side effects.**
30. **Do not silently ignore errors.**
31. **Do not create God components.**
32. **Do not create dumping-ground code.**
33. **Do not create speculative abstractions.**
34. **Do not optimize prematurely.**
35. **Prefer readable code over clever code.**
36. **Preserve data integrity.**
37. **Keep tests meaningful and deterministic.**
38. **Do not change unrelated code.**
39. **Documentation must reflect actual behavior.**
40. **Every long-lived resource must have a clear lifecycle.**
41. **Every important state must have a clear owner.**
42. **Every consequential external operation must have appropriate failure handling.**
43. **Every feature must remain understandable and maintainable.**

---

# 84. Final Principle

For every engineering decision, ask:

> **Is this the simplest clean, correct, secure, maintainable, reliable, and professional solution that satisfies the actual requirement?**

If yes, implement it.

If not, simplify it.

Do not add complexity merely because it looks sophisticated.

Do not abstract prematurely.

Do not optimize prematurely.

Do not duplicate unnecessarily.

Do not hide behavior.

Do not ignore failures.

Do not allow uncontrolled memory or resource growth.

Write software that another professional developer can open, understand, modify, test, and trust.
