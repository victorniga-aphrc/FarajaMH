### Project status update – FarajaMH

We’ve made strong progress on the core application and are now moving into refinement and hardening.

**What’s done so far**

- **Role-based experience is in place**  
  - Admins, clinicians, and patients each see the views and data that are appropriate for them.  
  - Usernames are used in the interface instead of email addresses for better privacy.

- **Clinician and admin tools are much richer**  
  - Admins can manage all users (create and edit clinicians, reset access, see usage patterns).  
  - Clinicians have their own dashboard, can view their past conversations, and see symptom trends from their own cases.

- **Patient management is now supported**  
  - Clinicians can create simple patient identifiers (e.g. P01, P02) and attach each conversation to a specific patient.  
  - Admins can see conversations across all clinicians and patients, which is important for oversight and evaluation.

- **Live Mic and simulated modes are working end to end**  
  - The app supports live transcription (speaking instead of typing), plus a simulated mode where the clinician gets suggested follow-up questions.  
  - Screening results (for depression, anxiety and psychosis) are shown continuously as the conversation unfolds.

- **Operational plumbing is in place**  
  - The app runs reliably both locally and in Docker, with logs, migrations, and environment-based configuration in place.  
  - We have scripts and CI checks to make starting, stopping, and testing the app easier.

**What we are currently working on**

- **User interface improvements**  
  We’re modernizing the UI so it feels cleaner, easier to navigate, and able to scale as we add more features and data.

- **Reliability of saved conversations and screening results**  
  We’re tightening up how conversations and their associated screening outputs are stored and retrieved, to ensure every session is correctly saved and always shows the right results when reopened.

- **Support for multiple concurrent conversations**  
  We’re testing and tuning how the app behaves when there are many conversations happening at the same time (for example, multiple tabs, multiple patients, or several clinicians working simultaneously), so it remains stable and responsive under load.

