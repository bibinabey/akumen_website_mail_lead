Folder : AKUMEN_MAIL_LEAD_FUN_APP

Sub Folder : mail_processor
function_app.py is the main file and this is where the webhook hit and it will have the message_id and this message_id is added to a queue and from this another api is called and the email is processed

local.settings.json
this contain env variables and "OUTLOOK_QUEUE_NAME": "queue-trigger-test" is queue in azure we use

requirements
azure-functions
azure-storage-queue
needed to install before running

how to run 

cd mail_processor
func start

(after this in a new terminal ngrok http 7071 to get https url and this url need to use when calling the create_sub_id.py file)




