# Phase 00 learning notes

Complete this after reviewing the implementation, in your own words.

1. What did I build, and why is it structured this way?
Built the setup of the project including the reading of Settings, logging system and test. And also a cli entry point. 

2. What is canonical data versus derived data in Steward?
Canonical data comes from original markdown/pdf files, explicit user decisions, record and activity history basically where there is fact. Derived is from chunks, indexes and embeddings. If derived data vanishes canonical data must remain so it can be calculated again

3. Where does configuration state live, and what occurs at startup?
Configuration state live in Settings. at start up python reads the environment and look for values. If present set it as Setting is creatd.

4. What side effects occur in this phase, and what can fail?
WE read env, configures process logging and write the output to terminal, does not persist steward data. Possible failures include an invalid STEWARD_LOG_LEVEL,
  missing Python/dependencies, or failure to start the CLI.
  No vault or database failure exists yet because neither is
  implemented

5. Why is LangGraph deliberately absent?
now we are building the deterministic portion and testing everything before we insert agents to make use of them
