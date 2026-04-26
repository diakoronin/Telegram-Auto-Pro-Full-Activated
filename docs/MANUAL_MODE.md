# Manual mode (links)

- With **`MANUAL_MODE_ENABLED=true`**, admins see the manual sales menu in Telegram.
- **`ALLOW_USER_MANUAL_PRODUCTS=false`** by default — normal users do not buy manual products.
- **Import:** in Telegram admin, use manual TXT import — first line: `manual_server_id,manual_plan_id`, then one link per line.
- **Delivery:** admin flow in the bot picks an unused link (see admin handlers).
- Used links cannot be reused.
