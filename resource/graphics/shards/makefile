# Restyled item icons from tools/iconforge (forge.py install). They keep the original
# file names, so copying them last (shards is the last of DIRS in ..\makefile)
# replaces the contributors' versions in the client run folder.

TOPDIR=..\..\..
!include $(TOPDIR)\common.mak

all:
	@-for %i in (*.bgf) do @$(CP) %i $(CLIENTRUNDIR)\resource >nul

clean:
