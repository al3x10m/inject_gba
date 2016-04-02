
import	binascii
import	collections
import	ctypes
import	hashlib
import	html
import	mt19937
import	optparse
import	os
import	struct
import	sys
import	yaml
import	zlib

import	global_vars

#
# Define our object classes
#
# Note: we can't use __slots__ to save memory because that breaks __dict__ which we need for serialization
#
class	TypeValue(yaml.YAMLObject):
	yaml_tag = u'!TV'
	def	__init__(self, t, v):
		self.t = t
		self.v = v
	def	__repr__(self):
		return "%s(t=%r, v=%r)" % (self.__class__.__name__, self.t, self.v)

class	NameObject(yaml.YAMLObject):
	yaml_tag = u'!NO'
	def	__init__(self, ni, o):
		self.ni = ni	# index into names[]
		self.o = o	# object
	def	__repr__(self):
		return "%s(ni=%r, o=%r)" % (self.__class__.__name__, self.ni, self.o)

class	FileInfo(yaml.YAMLObject):
	yaml_tag = u'!FI'
	def	__init__(self, ni, dn, l, o,):
		self.ni	= ni	# index into names[]
		self.dn	= dn	# diskname in BASE+NNNN form
		self.l	= l	# original length
		self.o	= o	# original offset
	def	__repr__(self):
		return "%s(ni=%r, dn=%r l=%r, o=%r)" % (self.__class__.__name__, self.ni, self.dn, self.l, self.o)

#
# get the size of an int in bytes
#
def	getIntSize(v):
	for s in range(1, 8):
		if v < (1 << (8 * s)):
			return s

class	buffer_packer():
	def __init__(self):
		self._buffer = []
		self._offset = 0	# points to the *next* byte to write

	def __call__(self, fmt, data):
		packed_data = struct.pack(fmt, data)
		packed_length = len(packed_data)
		self._buffer[self._offset : self._offset + packed_length] = packed_data
		self._offset += packed_length

	def	length(self):
		return len(self._buffer)

	def	seek(self, offset):
		if len(self._buffer) < offset:
			self._buffer = self._buffer + [0] * (offset - len(self._buffer) + 1)
		self._offset = offset

	def	tell(self):
		return self._offset


'''
<	Little endian
>	Big endian
b	signed char
B	unsigned char
H	unsigned 2 bytes
I	unsigned 4 bytes
L	unsigned 4 bytes
Q	unsigned 8 bytes
'''

class	buffer_unpacker():
	def __init__(self, buffer):
		self._buffer = buffer
		self._offset = 0

	def __call__(self, fmt):
		result = struct.unpack_from(fmt, self._buffer, self._offset)
		self._offset += struct.calcsize(fmt)
		return result

	def	seek(self, offset):
		if offset >= 0 and offset < len(self._buffer):
			self._offset = offset
		return self._offset

	def	tell(self):
		return self._offset

	def	length(self):
		return len(self._buffer)

	def	data(self):
		return self._buffer[self._offset : ]

	# Get the next output without moving the offset
	def	peek(self, fmt):
		off = self.tell()
		out = self(fmt)
		self.seek(off)
		return out

	def	peek16(self):
		r = min(16, (self.length() - self.tell()))
		if r > 0:
			return self.peek('<%dB' % r)
		return "EOF"

# mdf\0
# PSB\0
class	HDRLEN():
	def	__init__(self):
		self.offset0		= 0
		self.offset1		= 0
		self.signature		= []
		self.length		= 0

	def	pack(self, packer):
		packer('>4s',	self.signature)
		packer('<I',	self.length)

	def	unpack(self, unpacker):
		self.offset0		= unpacker.tell()
		self.signature		= unpacker('>4s')[0]
		self.length		= unpacker('<I')[0]
		self.offset1		= unpacker.tell()


'''

From exm2lib
struct PSBHDR {
	unsigned char signature[4];
	unsigned long type;
	unsigned long unknown1;
	unsigned long offset_names;
	unsigned long offset_strings;
	unsigned long offset_strings_data;
	unsigned long offset_chunk_offsets;
	unsigned long offset_chunk_lengths;
	unsigned long offset_chunk_data;
	unsigned long offset_entries;
};


'''

class	PSB():
	def	__init__(self):
		self.header		= PSB_HDR()

		self.names		= []	# list of strings indexed by NameObject.ni
		self.strings		= [] 	# list of strings index by Type 21-24
		self.chunkdata		= []	# raw data indexed by Type 25-28
		self.chunknames		= []	# CNNNN filenames for each chunk
		self.entries		= None
		self.fileinfo		= []	# Stash of FileInfo objects (easier than walking the entries tree)
		self.filedata		= []	# uncompress/unencrypted data for each file
		#self.filenames		= []	# FNNNN filenames for each file_info 
		# Stashed when unpacking, used after to load the file data
		#self.fileoffsets	= []
		#self.filelengths	= []
		#self.filenameindex	= []
		# Variables used for repacking
		self.new_names		= None
		self.new_strings	= None
		self.new_chunks		= None
		self.new_files		= None

	def	__str__(self):
		o = "PSB:\n"
		o += str(self.header)
		for i in range(0, len(self.names)):
			o += "Name %d %s\n" % (i, self.names[i])
		#o += "Strings %s\n" % str(self.strings)
		#o += "Strings Data %s\n" % self.strings_data
		for i in range(0, len(self.strings)):
			o += "String %d %s\n" % (i, self.strings[i])
		#o += "Chunk offsets %s\n" % str(self.chunk_offsets)
		#o += "Chunk lengths %s\n" % str(self.chunk_offsets)
		o += "Entries %s\n" % str(self.entries)
		#for i in range(0, self.entries.names.count):
		#	s = self.entries.names.values[i]
		#	o += "%d %d %s\n" % (i, s, self.name[s])
		return o

	def	pack(self):
		packer = buffer_packer()

		# Encrypt/compress/concat our files

		# Write out our dummy header
		self.header.signature = b'PSB\x00'
		self.header.pack(packer)

		# Pack the array of names
		#self.pack_names(unpacker)

		# Pack the array of strings
		self.pack_strings(packer)

		# Pack the array of chunks
		self.pack_chunks(packer)

		# Pack our tree of entries
		#self.pack_entries(unpacker)

		# Rewrite the header with the correct offsets
		packer.seek(0)
		self.header.pack(packer)

		psb_data = bytearray(packer._buffer)
		bin_data = bytearray([])

		return psb_data, bin_data

	def	unpack(self, psb_data):
		unpacker = buffer_unpacker(psb_data)

		if global_vars.options.verbose:
			print("Parsing header:")
			l = len(unpacker.data())
			print("PSB data length %d 0x%X" % (l, l))

		self.header.unpack(unpacker)
		if self.header.signature != b'PSB\x00':
			if global_vars.options.debug:
				print("Not a PSB file")
				print(self.header.signature)
			return
		if global_vars.options.verbose:
			print(self.header)

		# Read in the arrays of names
		# These are a complex structure used to remove duplicate prefixes of the file names
		self.unpack_names(unpacker)

		# Read in the array of strings
		self.unpack_strings(unpacker)

		# Read in the array of chunks
		self.unpack_chunks(unpacker)

		# Read in our tree of entries
		self.unpack_entries(unpacker)

	def	print_yaml(self):
		# Create a top-level dict to dump
		level0 = {
			'names':	self.names,
			'strings':	self.strings,
			'chunknames':	self.chunknames,
			'entries':	self.entries,
			'fileinfo':	self.fileinfo,
		}
		return yaml.dump(level0)

	def	load_yaml(self, data):
		# FIXME - use yaml.safe_load
		level0 = yaml.load(data)
		if isinstance(level0, dict):
			self.names		= level0['names']
			self.strings		= level0['strings']
			self.chunknames		= level0['chunknames']
			self.entries		= level0['entries']
			self.fileinfo		= level0['fileinfo']

	# Read in our chunk files
	def	read_chunks(self, base_dir):
		self.chunkdata = []
		for cn in self.chunknames:
			filename = os.path.join(base_dir, cn)
			if global_vars.options.verbose:
				print("Reading chunk '%s'" % filename)
			data = open(filename, 'rb').read()
			self.chunkdata.append(data)

	# Write out our chunk files
	def	write_chunks(self, base_dir):
		for i, fn in enumerate(self.chunknames):
			filename = os.path.join(base_dir, fn)
			if os.path.isfile(filename):
				print("File '%s' exists, not over-writing" % filename)
			else:
				if global_vars.options.verbose:
					print("Writing file %s" % filename)
				open(filename, 'wb').write(self.chunkdata[i])

	# Read in our subfiles and update the fileinfo[]
	def	read_subfiles(self, base_dir):
		bin_data	= []
		for i, fi in enumerate(self.fileinfo):
			if global_vars.options.verbose:
				print("Reading in '%s'" % (fi.dn))

			# Read in the raw data
			fd = open(os.path.join(base_dir, fi.dn), 'rb').read()

			if global_vars.options.verbose:
				print("Raw length %d 0x%X" % (len(fd), len(fd)))

			# Compress the data
			if fi.dn.endswith('.m'):
				if fi.dn.endswith('.jpg.m'):
					fd = compress_data(fd, 0)
				else:
					fd = compress_data(fd, 9)

			if global_vars.options.verbose:
				print("Compressed length %d 0x%X" % (len(fd), len(fd)))

			# Obfuscate the data using the original filename for the seed
			unobfuscate_data(fd, self.names[fi.ni])

			# Remember the unpadded length
			new_length = len(fd)

			if new_length != fi.l:
				print("<<< old length %d 0x%X" % (fi.l, fi.l))
				print("<<< new length %d 0x%X" % (new_length, new_length))

			# Pad the data to a multiple of 0x800 bytes
			p = len(fd) % 0x800
			if p:
				fd += b'\x00' * (0x800 - p)
			if global_vars.options.verbose:
				print("Padded length %d 0x%X" % (len(fd), len(fd)))

			# Update the PSB's FileInfo with the new length, offset
			self.fileinfo[i].o = len(bin_data)
			self.fileinfo[i].l = new_length

			# Save the compressed/encrypted/padded data
			bin_data.extend(fd)

		return bin_data

	# Write out our subfiles
	def	write_subfiles(self, base_dir, bin_data):
		for i, fi in enumerate(self.fileinfo):
			#print("%d %s" % (i, fi))
			filename = os.path.join(base_dir, fi.dn)
			if os.path.isfile(filename):
				print("File '%s' exists, not over-writing" % filename)
			else:
				if global_vars.options.verbose:
					print("Writing file %s" % filename)

				# If it looks like our rom, output the filename
				if 'system/roms' in self.names[fi.ni]:
					print("ROM in '%s'" % filename)

				# Get the chunk of data from the alldata.bin file
				fd = bin_data[fi.o : fi.o + fi.l]
				#open(filename + '.1', 'wb').write(fd)

				# Unobfuscate the data using the original filename for the seed
				unobfuscate_data(fd, self.names[fi.ni])
				#open(filename + '.2', 'wb').write(fd)

				# Uncompress the data
				fd = uncompress_data(fd)

				# Write out the subfile
				open(filename, 'wb').write(fd)

	#
	# based on exm2lib get_number()
	#
	def	pack_object(self, packer, name, obj):
		t = obj.t
		if t >= 1 and t <= 3:
			packer('<B', t)
		elif t >=4 and t <= 12:
			# int, 0-8 bytes
			v = obj.v
			if v == 0:
				packer('<B', 4)
			else:
				s = getIntSize(v)
				packer('<B', 4 + s)
				packer('<%ds' % s, v.to_bytes(s, 'little'))
		elif t >= 13 and t <= 20:
			# array of ints, packed as size of count, count, size of entries, entries[]
			count = len(obj.v)
			s = getIntSize(count)
			packer('<B', 12 + s)
			packer('<%ds' % s, count.to_bytes(s, 'little'))
			# Find our biggest value
			if count:
				max_value = max(obj.v)
			else:
				max_value = 0
			# Pack the number of bytes in each value
			s = getIntSize(max_value)
			packer('<B', s + 12)
			# Pack each value
			for v in obj.v:
				packer('<%ds' % s, v.to_bytes(s, 'little'))
		elif t >= 21 and t <= 24:
			# index into 'strings' array (1-4 bytes)
			s = getIntSize(v)
			packer('<B', 20 + s)
			packer('<%ds' % s, v.to_bytes(s, 'little'))
		elif t >= 25 and t <= 28:
			# index into 'chunks' array, 1-4 bytes
			s = getIntSize(v)
			packer('<B', 24 + s)
			packer('<%ds' % s, v.to_bytes(s, 'little'))
		elif t == 29:
			# 0 byte float
			packer('<B', t)
		elif t == 30:
			# 4 byte float
			packer('<B', t)
			packer('f', obj.v)
		elif t == 31:
			# 8 byte float
			packer('<B', t)
			packer('d', obj.v)
		elif t == 32:
			# array of objects, written as array of offsets (int), array of objects
			packer('<B', t)
			# Get our list of objects
			v = obj.v
			# Build a list of offsets
			list_of_offsets = []
			list_of_objects	= []
			next_offset = 0
			for i in range(0, len(v)):
				o = v[i]
				# Pack our object into a temporary buffer to get the size
				tmp_packer = buffer_packer()
				self.pack_object(tmp_packer, name + "|%d" % i, o)
				# Remember our offset
				list_of_offsets.append(next_offset)
				# Remember our size for the next offset
				next_offset += tmp_packer.length()
				# Remember our object data
				list_of_objects.append(bytes(tmp_packer._buffer))
			# Pack the list of offsets
			self.pack_object(packer, '', TypeValue(13, list_of_offsets))
			# Pack the object data
			for oi in range(0, len(list_of_objects)):
				packer('<s', list_of_objects[oi])
		elif t == 33:
			# array of name/object pairs, written as array of name indexes, array of offsets, array of objects
			packer('<B', t)
			# Get our list of objects
			v = obj.v
			next_offset = 0
			list_of_names   = []
			list_of_offsets = []
			list_of_objects	= []
			for o in v:
				obj_name_index = o.ni
				obj_name = o.ns
				obj_data = o.o
				if global_vars.options.verbose:
					print("<<< %s %s" % ('name', obj_name))
				# If the type33 is a file_info, each member is a file
				if name == '|file_info':
					assert(type(obj_data) == FileInfo)
					if global_vars.options.verbose:
						print('<<<', obj_data)
					# If we have a file, read it in and fix the offset/length before packing the object
					if self.new_files:
						print("Reading in '%s' for '%s'" % (obj_data.f, obj_name))
						# Read in the raw data
						fd = open(os.path.join(os.path.dirname(global_vars.options.basename), obj_data.f), 'rb').read()
						print("Raw length %d 0x%X" % (len(fd), len(fd)))
						# Compress the data
						if '.jpg.m' in obj_name:
							fd = compress_data(fd, 0)
						else:
							fd = compress_data(fd, 9)
						print("Compressed length %d 0x%X" % (len(fd), len(fd)))
						# Obfuscate the data using the filename for the seed
						unobfuscate_data(fd, obj_name)
						# Remember the unpadded length
						new_length = len(fd)
						# Pad the data to a multiple of 0x800 bytes
						p = len(fd) % 0x800
						if p:
							fd += b'\x00' * (0x800 - p)
						print("Padded length %d 0x%X" % (len(fd), len(fd)))
						# Add the compressed/encrypted/padded data to our new_files array
						self.new_files.append(fd)
						# Fix up the offset/length
						new_offset = 0
						for i in range(0, len(self.new_files) -1):
							new_offset += len(self.new_files[i])

						if new_offset != obj_data.o:
							print("<<< '%s' -> '%s'" % (obj_data.f, obj_name))
							print("<<< old offset %d 0x%X" % (obj_data.o, obj_data.o))
							print("<<< new offset %d 0x%X" % (new_offset, new_offset))

						if new_length != obj_data.l:
							print("<<< '%s' -> '%s'" % (obj_data.f, obj_name))
							print("<<< old length %d 0x%X" % (obj_data.l, obj_data.l))
							print("<<< new length %d 0x%X" % (new_length, new_length))

						obj_data = TypeValue(32, [TypeValue(4, new_offset), TypeValue(4, new_length)])
					else:
						obj_data = TypeValue(32, [TypeValue(4, obj_data.o), TypeValue(4, obj_data.l)])
				# Pack our object into a temporary buffer to get the size
				tmp_packer = buffer_packer()
				self.pack_object(tmp_packer, name + "|%s" % obj_name, obj_data)
				# Remember our name index
				list_of_names.append(obj_name_index)
				# Remember our offset
				list_of_offsets.append(next_offset)
				# Remember our size for the next offset
				next_offset = tmp_packer.length()
				# Remember our object data
				list_of_objects.append(bytes(tmp_packer._buffer))
			# Pack the list of names
			self.pack_object(packer, '', TypeValue(13, list_of_names))
			# Pack the list of offsets
			self.pack_object(packer, '', TypeValue(13, list_of_offsets))
			# Pack the object data
			for oi in range(0, len(list_of_objects)):
				packer('<s', list_of_objects[oi])
		else:
			print("Unknown type")
			print(t)
			assert(False)

	def	unpack_object(self, unpacker, name):
		offset = unpacker.tell()
		if global_vars.options.verbose:
			print(">>> %s @0x%X" % (name, unpacker.tell()))
			print(unpacker.peek16())
		t = unpacker.peek('<B')[0]
		if t >= 1 and t <= 3:
			# from exm2lib & inspection, length = 0, purpose unknown
			t = unpacker('<B')[0]
			v = 0
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value ?" % (name, offset, t))
			return TypeValue(t, None)
		elif t == 4:
			# int, 0 bytes
			t = unpacker('<B')[0]
			v = 0
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value %d 0x%X" % (name, offset, t, v, v))
			return TypeValue(t, 0)
		elif t >= 5 and t <= 12:
			# int, 1-8 bytes
			t = unpacker('<B')[0]
			v = int.from_bytes(unpacker('<%dB' % (t - 5 + 1)), 'little')
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value %d 0x%X" % (name, offset, t, v, v))
			return TypeValue(t, v)
		elif t >= 13 and t <= 20:
			# array of ints, packed as size of count, count, size of entries, entries[]
			t = unpacker('<B')[0]
			size_count = t - 12
			count = int.from_bytes(unpacker('<%dB' % size_count), 'little')
			size_entries = unpacker('<B')[0] - 12
			values = []
			for i in range(0, count):
				v = int.from_bytes(unpacker('<%dB' % size_entries), 'little')
				values.append(v)
			return TypeValue(t, values)
		elif t >= 21 and t <= 24:
			# index into strings array, 1-4 bytes
			t = unpacker('<B')[0]
			v = int.from_bytes(unpacker('<%dB' % (t - 21 + 1)), 'little')
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value string %d" % (name, offset, t, v))
			assert(v <= len(self.strings))
			return TypeValue(t, v)
		elif t >= 25 and t <= 28:
			# index into chunks array, 1-4 bytes
			t = unpacker('<B')[0]
			v = int.from_bytes(unpacker('<%dB' % (t - 25 + 1)), 'little')
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value chunk %d" % (name, offset, t, v))
			assert(v <= len(self.chunkdata))
			return TypeValue(t, v)
		elif t == 29:
			# float, 0 bytes?
			t = unpacker('<B')[0]
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value ?" % (name, offset, t))
			return TypeValue(t, 0.0)
		elif t == 30:
			# float, 4 bytes
			t = unpacker('<B')[0]
			v = unpacker('f')[0]
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value %f" % (name, offset, t, v))
			return TypeValue(t, v)
		elif t == 31:
			# float, 8 bytes
			t = unpacker('<B')[0]
			v = unpacker('d')[0]
			if global_vars.options.verbose:
				print(">>> %s @0x%X type %d value %f" % (name, offset, t, v))
			return TypeValue(t, v)
		elif t == 32:
			# array of objects
			# from exm2lib, array of offsets of objects, followed by the objects
			t = unpacker('<B')[0]
			offsets = self.unpack_object(unpacker, name + '|offsets')
			seek_base = unpacker.tell()
			if global_vars.options.verbose:
				print(">>> %s @0x%X (%d entries)" % (name, offset, len(offsets.v)))
			v = []
			for i in range(0, len(offsets.v)):
				o = offsets.v[i]
				if global_vars.options.verbose:
					print(">>> %s @0x%X entry %d:" % (name, offset, i))
				unpacker.seek(seek_base + o)
				v1 = self.unpack_object(unpacker, name + "|%d" % i)
				v.append(v1)
			return TypeValue(t, v)
		elif t == 33:
			# array of name-objects
			# from exm2lib, array of int name indexes, array of int offsets, followed by objects
			t = unpacker('<B')[0]
			names   = self.unpack_object(unpacker, name + '|names')
			offsets = self.unpack_object(unpacker, name + '|offsets')
			seek_base = unpacker.tell()
			assert(len(names.v) == len(offsets.v))
			if global_vars.options.verbose:
				print(">>> %s @0x%X (%d entries)" % (name, offset, len(names.v)))
			if name == '|file_info':
				# If we are a file_info list, each object is a type 32 collection containing the offset & length values of the file data in alldata.bin
				# We build a list of FileInfo objects in the PSB, and ignore the list in the tree.
				v = []
				for i, ni in enumerate(names.v):
					ns = self.names[ni]
					o = offsets.v[i]
					if global_vars.options.verbose:
						print(">>> %s|%s @0x%X entry %d:" % (name, ns, offset, i))
						print(unpacker.peek16())

					# Unpack the object at the offset
					unpacker.seek(seek_base + o)
					v1 = self.unpack_object(unpacker, name + "|%s" % ns)

					# Sanity check our object
					assert(v1.t == 32)
					assert(len(v1.v) == 2)
					assert(v1.v[0].t >= 4)
					assert(v1.v[0].t <= 12)
					assert(v1.v[1].t >= 4)
					assert(v1.v[1].t <= 12)

					# Build the name of our sub-file
					diskname = self.getFilename(i) + "_" + os.path.basename(ns)

					# Get the offset and length
					fo = v1.v[0].v
					fl = v1.v[1].v

					# Save the FileInfo in our stash, but not to our entries tree list
					self.fileinfo.append(FileInfo(ni, diskname, fl, fo))

			# For each entry in the name list...
			v = []
			for i, ni in enumerate(names.v):
				# Get the name string and the offset
				ns = self.names[ni]
				o = offsets.v[i]
				if global_vars.options.verbose:
					print(">>> %s|%s @0x%X entry %d:" % (name, ns, offset, i))
					print(unpacker.peek16())

				# Unpack the object at the offset
				unpacker.seek(seek_base + o)
				v1 = self.unpack_object(unpacker, name + "|%s" % ns)

				# Add the object to our list
				v.append(NameObject(ni, v1))

			return TypeValue(t, v)

		else:
			print(">>> %s @0x%X" % (name, offset))
			print("Unknown type")
			print(unpacker.peek16())

	def	extractSubFiles(self, bin_data):
		for fi in self.fileinfo:
			fo = fi.o
			fl = fi.l
			assert(fo <= len(bin_data))
			assert((fo + fl) <= len(bin_data))

			ni = fi.ni
			ns = self.names[ni]

			# Extract the data chunk
			fd = bin_data[fo : fo + fl]
			#open(diskname + '.1', "wb").write(fd)

			# Unobfuscate the data using the original filename for the seed
			unobfuscate_data(fd, ns)
			#open(diskname + '.2', "wb").write(fd)

			# Uncompress the data
			fd = uncompress_data(fd)
			#open(diskname, "wb").write(fd)

			# Save the unobfuscated/uncompressed data to our files array
			self.filedata.append(fd)


	# Get the chunk filename
	def	getChunkFilename(self, chunk_index):
		if global_vars.options.basename:
			name = "%s_C%4.4d" % (os.path.basename(global_vars.options.basename), chunk_index)
		else:
			name = "%s_C%4.4d" % ('BASE', chunk_index)
		return name
		
	# Get the sub-file filename
	def	getFilename(self, file_index):
		if global_vars.options.basename:
			name = "%s_F%4.4d" % (os.path.basename(global_vars.options.basename), file_index)
		else:
			name = "%s_F%4.4d" % ('BASE', file_index)
		return name
		
	def	pack_chunks(self, packer):
		# Build a lists of offsets and lengths
		offsets = []
		lengths	= []
		offset = 0
		for i in range(0, len(self.chunkdata)):
			l = len(self.chunkdata[i])
			offsets.append(offset)
			lengths.append(l)
			offset += l

		# Pack our offsets array
		self.header.offset_chunk_offsets	= packer.tell()
		self.pack_object(packer, 'chunk_offsets', TypeValue(13, offsets))

		# Pack our lengths array
		self.header.offset_chunk_lengths	= packer.tell()
		self.pack_object(packer, 'chunk_lengths', TypeValue(13, lengths))

		# Pack our data
		self.header.offset_chunk_data		= packer.tell()
		for i in range(0, len(self.chunkdata)):
			packer('<%ds' % len(self.chunks[i]), self.chunkdata[i])
		
	def	unpack_chunks(self, unpacker):
		self.chunkdata		= []

		# Read in our chunk offsets array (this may be empty)
		unpacker.seek(self.header.offset_chunk_offsets)
		chunk_offsets = self.unpack_object(unpacker, 'chunk_offsets')
		if global_vars.options.verbose:
			print("Chunk offsets count %d" % len(chunk_offsets.v))
			for i in range(0, len(chunk_offsets.v)):
				print("Chunk offset %d = %d 0x%X" % (i, chunk_offsets.v[i], chunk_offsets.v[i]))


		# Read in our chunk lengths array (this may be empty)
		unpacker.seek(self.header.offset_chunk_lengths)
		chunk_lengths = self.unpack_object(unpacker, 'chunk_lengths')
		if global_vars.options.verbose:
			print("Chunk lengths count %d" % len(chunk_lengths.v))
			for i in range(0, len(chunk_lengths.v)):
				print("Chunk length %d = %d 0x%X" % (i, chunk_lengths.v[i], chunk_lengths.v[i]))

		assert(len(chunk_offsets.v) == len(chunk_lengths.v))

		# If we have chunk data, split it out
		if len(chunk_offsets.v) > 0 and self.header.offset_chunk_data < len(unpacker.data()):
			for i in range(0, len(chunk_offsets.v)):
				o = chunk_offsets.v[i]
				l = chunk_lengths.v[i]

				# Save the chunk data
				unpacker.seek(self.header.offset_chunk_data + o)
				d = unpacker.data()[:l]
				self.chunkdata.append(d)

				# Save the chunk filename
				self.chunknames.append(self.getChunkFilename(i))

	def	unpack_entries(self, unpacker):
		unpacker.seek(self.header.offset_entries)
		self.entries = self.unpack_object(unpacker, '')

	def	unpack_names(self, unpacker):

		unpacker.seek(self.header.offset_names)

		nt = PSB_NameTable()
		nt.offsets	= self.unpack_object(unpacker, 'offsets').v
		nt.jumps	= self.unpack_object(unpacker, 'jumps').v
		nt.starts	= self.unpack_object(unpacker, 'starts').v

		# Decode the 3 arrays into simple strings
		self.names		= []
		for i in range(0, len(nt.starts)):
			s = nt.get_name(i)
			self.names.append(s)
			if global_vars.options.verbose:
				print("Name %d %s" % (i, s))

	#
	# Pack our strings[] array, and update our header with the offsets
	#
	def	pack_strings(self, packer):
		# Build the list of offsets
		offsets = []
		offset = 0
		for s in self.strings:
			se = s.encode('utf-8')
			l = len(se) +1	# +1 for the NUL byte
			offsets.append(l)
			offset += l

		# Pack our offsets array object
		self.header.offsets_strings		= packer.tell()
		self.pack_object(packer, 'strings', TypeValue(13, offsets))

		# Pack our data
		self.header.offsets_strings_data	= packer.tell()
		for s in self.strings:
			se = s.encode('utf-8')
			l = len(se) +1	# +1 for the NUL byte
			packer('<%ds' % l, se)

	def	unpack_strings(self, unpacker):
		self.strings	= []

		unpacker.seek(self.header.offset_strings)
		strings_array	= self.unpack_object(unpacker, 'strings')

		if global_vars.options.verbose:
			print("Parsing strings array (%d)" % len(strings_array.v))
		# Read in each string
		for i in range(0, len(strings_array.v)):
			o = strings_array.v[i]
			# Create a python string from the NUL-terminated C-string at offset
			unpacker.seek(self.header.offset_strings_data + o)
			d = unpacker.data();
			for j in range(0, len(d)):
				if d[j] == 0:
					s = d[:j].decode('utf-8')
					self.strings.append(s)
					if global_vars.options.verbose:
						print("String %d  @0x%X %s" % (i, o, s))
					break

class	PSB_HDR():
	def	__init__(self):
		self.signature			= []
		self.type			= 0
		self.unknown1			= 0
		self.offset_names		= 0
		self.offset_strings		= 0
		self.offset_strings_data	= 0
		self.offset_chunk_offsets	= 0
		self.offset_chunk_lengths	= 0
		self.offset_chunk_data		= 0
		self.offset_entries		= 0

	def	__str__(self):
		o = "PSB header:\n"
		o += "signature %s\n"			% self.signature
		o += "type 0x%X\n"			% self.type
		o += "unknown1 0x%X\n"			% self.unknown1
		o += "offset_names 0x%X\n"		% self.offset_names
		o += "offset_strings 0x%X\n"		% self.offset_strings
		o += "offset_strings_data 0x%X\n"	% self.offset_strings_data
		o += "offset_chunk_offsets 0x%X\n"	% self.offset_chunk_offsets
		o += "offset_chunk_lengths 0x%X\n"	% self.offset_chunk_lengths
		o += "offset_chunk_data 0x%X\n"		% self.offset_chunk_data
		o += "offset_entries 0x%X\n"		% self.offset_entries
		return o


	def	pack(self, packer):
 		packer('>4s',	bytes(self.signature))
 		packer('<I',	self.type)
 		packer('<I',	self.unknown1)
 		packer('<I',	self.offset_names)
 		packer('<I',	self.offset_strings)
 		packer('<I',	self.offset_strings_data)
 		packer('<I',	self.offset_chunk_offsets)
 		packer('<I',	self.offset_chunk_lengths)
 		packer('<I',	self.offset_chunk_data)
 		packer('<I',	self.offset_entries)

	def	unpack(self, unpacker):
		self.signature			= unpacker('>4s')[0]
		self.type			= unpacker('<I')[0]
		self.unknown1			= unpacker('<I')[0]
		self.offset_names		= unpacker('<I')[0]
		self.offset_strings		= unpacker('<I')[0]
		self.offset_strings_data	= unpacker('<I')[0]
		self.offset_chunk_offsets	= unpacker('<I')[0]
		self.offset_chunk_lengths	= unpacker('<I')[0]
		self.offset_chunk_data		= unpacker('<I')[0]
		self.offset_entries		= unpacker('<I')[0]

#
# Get the XOR key for the given filename
#
def	get_xor_key(filename):
	fixed_seed	= b'MX8wgGEJ2+M47'	# From m2engage.elf
	key_length	= 0x50

	# Take our game hash_seed (always the same), and append our filename
	hash_seed = fixed_seed + os.path.basename(filename).lower().encode('latin-1')
	if global_vars.options.verbose:
		print("Using hash seed:\t%s" % hash_seed)

	# Take the MD5 hash of the seed+filename
	hash_as_bytes = hashlib.md5(hash_seed).digest()
	hash_as_longs = struct.unpack('<4I', hash_as_bytes)

	# Initialize our mersenne twister
	mt19937.init_by_array(hash_as_longs)

	# Start with an empty key buffer
	key_buffer = bytearray()

	# Initialize our key from the MT
	while len(key_buffer) < key_length:
		# Get the next 32 bits from our MT-PRNG, as a long
		l = mt19937.genrand_int32();
		# Convert to 4 bytes little-endian
		s = struct.pack('<L', l)

		# Add them to our key buffer
		key_buffer.extend(s)
	if global_vars.options.verbose:
		print("Using key:\t%s," % binascii.hexlify(bytes(key_buffer)))

	return key_buffer
	

#
# Unobfuscate the data
# This modifies the data in-place
#
def	unobfuscate_data(data, filename):
	header = HDRLEN()
	header.unpack(buffer_unpacker(data))

	if header.signature == b'mdf\x00':
		if global_vars.options.verbose:
			print("sig=%s" % header.signature)
			print("len=%d (0x%X)" % (header.length, header.length))

		key_buffer = get_xor_key(filename)

		# For each byte after the HDRLEN, XOR in our key
		key_len = len(key_buffer)
		for i in range(len(data) - header.offset1):
			data[i + header.offset1] ^= key_buffer[i % key_len]

#
# Compress the data and prepend a mdf header
#
def	compress_data(data, level = 9):
	packer = buffer_packer()

	# Create a header
	header = HDRLEN()
	header.signature = b'mdf\x00'
	header.length = len(data)
	header.pack(packer)

	# Compressed the data
	try:
		compressed = zlib.compress(data, level)
		packer('<%ds'% len(compressed), compressed)
	except Exception as e:
		# We could not compress it, use the uncompressed data
		print("Compression failed", e)
		packer('<%ds' % len(data), data)

	return bytearray(packer._buffer)

#
# Uncompress the data
# This returns a separate set of data
# (Daft python call-by-object)
#
def	uncompress_data(data):
	header = HDRLEN()
	header.unpack(buffer_unpacker(data))

	if header.signature == b'mdf\x00':
		# FIXME - need to test if the data really is compressed
		# (Skip the 8 byte MDF header)
		uncompressed = zlib.decompress(bytes(data[header.offset1 : ]))
		if (len(uncompressed) != header.length):
			print("Warning: uncompressed length %d does not match header length %d" % (len(uncompressed), header.length))
		if global_vars.options.verbose:
			print("Uncompressed Length: %d 0x%X" % (len(uncompressed), len(uncompressed)))
		return uncompressed
	else:
		# Return the data as-is
		return data


#
# Observations:
#
# 1. The jumps can be forwards or backwards
#
# 2.  We constrain the position so the offset is always >= 1
# This is not critical to the algorithm, but avoids size/sign issues when extracting.
# (This is "e = b - d" below).
#
# 3. The same offset is applied to each branch which arrives at a given node.
#
# Because of (1) we can start searching for free locations from the start of the table each time.
#
# Because of (2) we must constrain our search for new table locations to more than the new character.
#
# Because of (3) the node's children must be stored with the same relative offsets.
# If we encode these strings
# A B C
# A B D
# A B F
# The C,D,F can not be contiguous, they must be stored C,D,?,F
# There is no requirement that the C,D,?,F are stored after A,B, only the relative spacing within one set of children is fixed
#
# The simple solution is to always add sets of children to the end of the table.
# Possible optimizations:
# Search the table for a gap large enough to hold the set.
# Insert the sets ordered by decreasing set size.
# Insert the sets ordered by decreasing min-max range.
# In practice, these are not needed.


# PSB_Node:
# This describes a single character in our 'names' table
class	PSB_Node:
	def	__init__(self):
		# These describe each character in our list of names
		self.id		= 0	# Our index into the PSB_NodeTree.nodes list
		self.p		= 0	# Our parent index into the PSB_NodeTree.nodes list
		self.cn		= []	# Our children (index into the PSB_NodeTree.nodes list)
		self.c		= 0	# This node's character
		# These are used to build the PSB_NameTable tables:
		self.ji		= 0	# This holds the index into the PSB_NameTable.jumps list
	def	__repr__(self):
		return "%s(id=%r, p=%r, cn=%r, c=%r)" % (self.__class__.__name__, self.id, self.p, self.cn, self.c)

# PSB_NodeTree:
class	PSB_NodeTree:
	def	__init__(self):
		self.nodes		= []	# List of PSB_Node objects
		self.starting_nodes	= []	# list of indexes into self.nodes

	def	reverse_walk(self, ni):
		if ni == 0:
			return ""
		else:
			c = self.nodes[ni].c
			return self.reverse_walk(self.nodes[ni].p) + chr(c)

	def	add_strings(self, names):

		# Start with an an empty root node
		self.nodes		= []
		self.nodes.append(PSB_Node())

		self.starting_nodes	= []

		# For each string in our list...
		#for name_idx, name_str in enumerate(sorted(names, key=len)):
		for name_idx in range(len(names)):
			name_str = names[name_idx]

			# Start searching the node tree from the top
			node_idx = 0

			# For each char in our string
			for c in name_str.encode('latin-1') + b'\x00':
				# Check if we match any of our children
				for child in self.nodes[node_idx].cn:
					if self.nodes[child].c == c:
						# Found match, use it
						node_idx = child
						break
				else:
					# Allocate a new node
					self.nodes.append(PSB_Node())
					next_idx = len(self.nodes)-1
					self.nodes[next_idx].id = next_idx

					# Set the new node's parent to us
					self.nodes[next_idx].p = node_idx

					# Add the new node to our list of children
					self.nodes[node_idx].cn.append(next_idx)

					# Set the new node to the new char
					self.nodes[next_idx].c = c

					# Point to the new node
					node_idx = next_idx
			else:
				# This is the last node for this string
				# Store the string# -> node
				self.starting_nodes.append(node_idx)

class	PSB_NameTable:
	def	__init__(self):
		self.jumps	= []
		self.offsets	= []
		self.starts	= []

	def	get_name(self, index):

		# Get the starting position
		a = self.starts[index]

		# Follow one jump to skip the terminating NUL
		# (Not critical to the walking algorithm)
		b = self.jumps[a]

		DEBUG_SEEN	= 1

		if DEBUG_SEEN:
			seen = [0] * len(self.jumps)

		accum = ""

		while b != 0:
			# Get our parent jump index
			c = self.jumps[b]

			# Get the offset from our parent
			d = self.offsets[c]

			# Get our char. (our jump index - parent's offset)
			e = b - d

			# Sanity check our character
			if e < 1:
				print("b: %d " % b, end="")
				print("c: %d " % c, end="")
				print("d: %d " % d, end="")
				print("e: %d " % e, end="")
				print("")

			# Check for loops in the jump table
			if DEBUG_SEEN:
				seen[b] = 1
				if seen[c]:
					print("Loop detected in jump table:")
					print("b: %d " % b, end="")
					print("c: %d " % c, end="")
					print("d: %d " % d, end="")
					print("e: %d " % e, end="")
					print("")
					return accum

			# Prepend our char to our string
			accum = chr(e) + accum

			# Move to our parent
			b = c

		return accum

	def	build_tables(self, names):
		self.jumps	= []
		self.offsets	= []
		self.starts	= []

		node_tree = PSB_NodeTree()
		node_tree.add_strings(names)

		self.build_jumps(node_tree)
		self.build_offsets(node_tree)
		self.build_starts(node_tree)


	def	build_jumps(self, node_tree):
		for ni in range(len(node_tree.nodes)):
			# Skip the root node
			if ni:
				# We may have already processed this node (but not our children) when processing our parent.
				# If we have not already processed this node, find a position for it
				if node_tree.nodes[ni].ji == 0:
					# Constrain the index so the index-char offset >= 1
					min_ji = node_tree.nodes[ni].c + 1

					# Extend the table if needed
					if len(self.jumps) <= min_ji:
						self.jumps.extend([None] * (min_ji - len(self.jumps) +1))

					# Find the first unused jump entry
					for ji in range(min_ji, len(self.jumps)):
						if self.jumps[ji] is None:
							break
					else:
						# We didn't find one, extend the table
						# Set our jump index to the next unused entry
						ji = len(self.jumps)
						# Extend the table by 1
						self.jumps.extend([None] * 1)

					# Save our node's jump index
					node_tree.nodes[ni].ji = ji
					# Set our jump value to our parent's jump index
					p = node_tree.nodes[ni].p
					pji = node_tree.nodes[p].ji
					self.jumps[ji] = pji

			# If we have >1 children, add space for the range of chars
			# We could search for a gap, but it would be very unlikely.
			cn_count = len(node_tree.nodes[ni].cn)
			if cn_count > 1:
				# Get the min, max of our children's characters
				c_min = min(node_tree.nodes[ci].c for ci in node_tree.nodes[ni].cn)
				c_max = max(node_tree.nodes[ci].c for ci in node_tree.nodes[ni].cn)

				# Get the index of the first child
				ji_first = len(self.jumps)

				# Constrain the index so the index-char offset >= 1
				if ji_first < (c_min +1):
					ji_first = c_min +1

				# Get the index of the last child
				ji_last = ji_first - c_min + c_max

				# Extend the jump table
				self.jumps.extend([None] * (ji_last - len(self.jumps) +1))

				# For each child...
				for ci in node_tree.nodes[ni].cn:
					# Set our child node's jump index
					ji_child = ji_first - c_min + node_tree.nodes[ci].c
					node_tree.nodes[ci].ji = ji_child
					# Set our child's jump target to ourselves
					self.jumps[ji_child] = node_tree.nodes[ni].ji

		# Fix any remaining None entries
		for ji in range(len(self.jumps)):
			if self.jumps[ji] is None:
				self.jumps[ji] = 0

	# Fill in the offsets table
	def	build_offsets(self, node_tree):

		# Start with a list of 0s
		self.offsets	= [0] * len(self.jumps)
		for ni in range(1, len(node_tree.nodes)):

			# Calculate our offset (jump index - character)
			o = node_tree.nodes[ni].ji - node_tree.nodes[ni].c

			# Sanity check this meets our >=1
			assert(o >= 1)

			# Get our parent
			p	= node_tree.nodes[ni].p

			# Get our parent's jump index
			pji	= node_tree.nodes[p].ji
			assert(pji >= 0)
			assert(pji < len(self.offsets))

			# Record the offset in our parent
			self.offsets[pji] = o

	# Fill in the starts table
	def	build_starts(self, node_tree):

		# Start with an empty list
		self.starts	= []

		# For each starting node in the node tree
		for si in node_tree.starting_nodes:

			# Get the jump index from the starting node
			ji = node_tree.nodes[si].ji

			# Add it to our list
			self.starts.append(ji)
