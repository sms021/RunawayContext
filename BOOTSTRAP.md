# SuperContext Bootstrap Prompt

Copy everything below the line and paste it as your first message to any AI assistant (Claude Code, Cursor, ChatGPT, Copilot Chat, etc.). Place the `SUPERCONTEXT.md` file somewhere the AI can read it first — your project root or home directory works fine.

---

## Copy from here ↓

I want you to set up a persistent memory system for our work together. The full guide is in the file `SUPERCONTEXT.md` — read it now before doing anything else.

Once you've read it, walk me through setup by asking me these questions one at a time (don't dump them all at once):

1. **What AI tool am I using?** (Claude Code, Cursor, GitHub Copilot, Codex, ChatGPT, something else)
2. **What's my project about?** (A few sentences is fine — what am I building, what language/stack, where does it live on my machine)
3. **How experienced am I?** (Beginner, intermediate, advanced — with coding in general, and with this specific project)
4. **Are there things I already know I want you to always do or never do?** (Preferences, pet peeves, past frustrations with AI assistants)
5. **Do I have multiple projects or just one?** (This determines whether we need project-level brains right away)
6. **Do I have any databases, APIs, or external services?** (This determines whether we need a Knowledge Store)

After I answer, implement the system step by step:

### Step 1: Create my Constitution (Tier 1)
Build the global instruction file for my specific AI tool (use the correct filename and location from Section 8 of the guide). Include:
- My project info
- My preferences and rules (from question 4)
- The knowledge routing table
- The project context protocol
- Any tool/environment configs from question 6

**Show me the file before saving it.** I want to review and adjust.

### Step 2: Create my Living Memory (Tier 2)
Set up the memory index file in the right location for my tool. Start it mostly empty but with the correct structure and a comment explaining how it works. Pre-populate it with anything I mentioned in question 4 that qualifies as a behavioral preference or gotcha.

**Save this one directly** — I'll see it grow over time.

### Step 3: Decide on Tier 3 and 4
Based on my answers:
- If I have **one simple project**: skip Tier 3 for now, mention we'll create a Project Brain when the project gets complex enough
- If I have **multiple projects or one complex one**: create a starter Project Brain for my main project
- If I mentioned **databases or APIs**: suggest whether I need a Knowledge Store now or later, and at what level (markdown files vs SQLite)

### Step 4: Teach me the habits
After setup, give me a short (5 bullet) cheat sheet of habits that keep the system alive:
- When to tell you to remember something
- When to update the project brain
- When to check memory
- How to correct you so the correction sticks
- Signs the system needs maintenance

### Important rules for this setup:
- **Ask one question at a time.** Wait for my answer before asking the next.
- **Don't overwhelm me.** If I seem new to this, keep explanations short and jargon-free.
- **Adapt to my tool.** Use the exact filenames and locations that work for my specific AI tool.
- **Start simple.** Tier 1 + 2 only unless my answers clearly warrant more. We can always add complexity later.
- **Keep the Constitution under 200 lines.** If you're tempted to add more, it probably belongs in a lower tier.
- **After setup is complete**, tell me: "Your AI memory system is live. From now on, when you correct me or I learn something important, I'll save it to memory. You can also tell me 'remember this' anytime."
