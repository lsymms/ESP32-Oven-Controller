import storage
import usb_hid

storage.disable_usb_drive()
usb_hid.disable()
storage.remount("/", readonly=False)
