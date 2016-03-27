#!/usr/bin/env python3

import	binascii
import	ctypes
import	hashlib
import	html
import	json
import	mt19937
import	optparse
import	os
import	struct
import	sys
import	zlib

import	psb
import	global_vars


def	extract_psb(psb_filename):

	if global_vars.options.verbose:
		print("Reading file %s" % psb_filename)

	psb_file_data = bytearray(open(psb_filename, 'rb').read())

	psb.unobfuscate_data(psb_file_data, psb_filename)

	psb_file_data = psb.uncompress_data(psb_file_data)

	header = psb.HDRLEN()
	header.unpack(psb.buffer_unpacker(psb_file_data))
	if header.signature != b'PSB\x00':
		print("PSB header not found")
		return

	base_filename = None
	bin_filename  = None
	json_filename = None
	b1, e1 = os.path.splitext(psb_filename)
	if (e1 == '.m'):
		b2, e2 = os.path.splitext(b1)
		if (e2 == '.psb'):
			base_filename = b2
			bin_filename  = b2 + ".bin"
			json_filename = b2 + ".json"

	if os.path.isfile(bin_filename):
		if global_vars.options.verbose:
			print("Reading file %s" % bin_filename)
		bin_file_data = bytearray(open(bin_filename, 'rb').read())
	else:
		bin_file_data = None

	mypsb = psb.PSB(base_filename)
	mypsb.unpack(psb_file_data, bin_file_data)

	if global_vars.options.json:
		j = open(json_filename, 'wt')
		mypsb.print_json(j)

	if global_vars.options.test:
		psb_data, bin_data = mypsb.pack()
		open(psb_filename + '.out', 'wb').write(psb_data)
		open(bin_filename + '.out', 'wb').write(bin_data)

def	main():

	class MyParser(optparse.OptionParser):
		def format_epilog(self, formatter):
			return self.expand_prog_name(self.epilog)

	parser = MyParser(usage='Usage: %prog [options] <psb filename>', epilog=
"""
Examples:

%prog -j alldata.psb.m
This will read alldata.psb.m and alldata.bin, and write out alldata.json

%prog -f -j alldata.psb.m
This will read alldata.psb.m and alldata.bin, and write out alldata.json with all sub-files in alldata.json_0000 etc

""")
	parser.add_option('-f',	'--files',	dest='files',		help='write subfiles to alldata_NNNN',		action='store_true',	default=False)
	parser.add_option('-j',	'--json',	dest='json',		help='write JSON to alldata.json',		action='store_true',	default=False)
	parser.add_option('-p',	'--parse',	dest='parse',		help='parse sub PSB files',			action='store_true',	default=False)
	parser.add_option('-q',	'--quiet',	dest='quiet',		help='quiet output',				action='store_true',	default=False)
	parser.add_option('-t',	'--test',	dest='test',		help='test repacking PSB',			action='store_true',	default=False)
	parser.add_option('-v',	'--verbose',	dest='verbose',		help='verbose output',				action='store_true',	default=False)
	(global_vars.options, args) = parser.parse_args()

	if not args:
		parser.print_help()

	for psb_filename in args:
		extract_psb(psb_filename)

if __name__ == "__main__":
	main()
