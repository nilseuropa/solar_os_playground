# Sollama

Local Ollama chat for SolarOS. It is aimed at a wide 1-bit panel: replies print as tokens by default, prompt wraps and conversations are saved on the msd.

The first launch asks for the server. Ollama must listen on the network, not only on localhost ...

```sh
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```
The address is `http://HOST:11434`. 

Chats and settings are stored in `/sdcard/sollama` when a card is mounted, otherwise in `/sollama`. 

## Keys + Function

- Enter sends. Esc opens the menu, and stops a reply that is still printing.
- Up and down scroll. This works while a reply is arriving.
- Left and right move the cursor. Ctrl+Left and Ctrl+Right jump by whole word.
- A long prompt wraps above the input line.
- Persistant system prompts, given to each model on start of chat.

Menu: models, saved chats, new chat, edit last, server, folder, system prompt, token or full print, follow tail, token cap, quit.

Edit last does not replace the old reply until the new send succeeds. A failed request puts the prompt back in the input line.
