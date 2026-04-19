- This should be examined as not only a litellm adapter, but as a proxy for all adapters, 
  other examples of future apps are listed in council/eric-questions.md at the bottom. 
  API keys will potentially be used across multiple apps, unless routed through a service
  like litellm. We should be mindful of a setup that is currently on the VPS is the
  same opanai key is used in litellm, n8n and open webui. If librechat, anythingllm, 
  or other apps are added, they will also use the same api keys for testing, but also 
  have the potential for different keys. In the walkthrough now the choice of litellm, 
  n8n, open webui, etc. is made during the bootstrap process. The next step is choosing 
  an env file or inputting keys, but this might be a friction point and potentially cause
  confusion at some point and make the code too bulky and unmanagable. My questions would be:

  1. What is the best method for handling API keys and other secrets across multiple apps
     that  have env files scattered, use the same keys and reduce steps for the users?

  2. Since we are opening subumbra proxy to the host, can post bootstrap be moved into the
     docker container and still have the same functionality and security? This might be for 
     a future round.

  3. Would it be easiest to just read the current env or config files, replace the keys with 
     the encrypted keys, and then shred the old file? This would keep the same env or config
     file name and location, just with the encrypted keys. The access key could be added to that env and allow for secure communication between the app and the subumbra proxy and the app is added within subumbra on setup. This might be for a future round.

  4. If an API key is reused accross multiple apps, does the key get encrypted multiple times
     for every app, or do we keep a single encrypted key within subumbra to make the call
     to the api provider?
