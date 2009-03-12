
import os
from cStringIO import StringIO
import pickle
from twisted.trial import unittest
from allmydata.test.no_network import GridTestMixin
from allmydata.util import fileutil
from allmydata.scripts import runner, debug
from allmydata.scripts.common import get_aliases
from twisted.internet import defer, threads # CLI tests use deferToThread
from allmydata.interfaces import IDirectoryNode


class CLITestMixin:
    def do_cli(self, verb, *args, **kwargs):
        nodeargs = [
            "--node-directory", self.get_clientdir(),
            ]
        if verb == "debug":
            argv = [verb, args[0]] + nodeargs + list(args[1:])
        else:
            argv = [verb] + nodeargs + list(args)
        stdin = kwargs.get("stdin", "")
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdin=StringIO(stdin),
                                  stdout=stdout, stderr=stderr)
        def _done(rc):
            return rc, stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

class Consolidate(GridTestMixin, CLITestMixin, unittest.TestCase):

    def writeto(self, path, data):
        d = os.path.dirname(os.path.join(self.basedir, "home", path))
        fileutil.make_dirs(d)
        f = open(os.path.join(self.basedir, "home", path), "w")
        f.write(data)
        f.close()

    def writeto_snapshot(self, sn, path, data):
        p = "Backups/fluxx/Archives/2009-03-%02d 01.01.01/%s" % (sn, path)
        return self.writeto(p, data)

    def do_cli_good(self, verb, *args, **kwargs):
        d = self.do_cli(verb, *args, **kwargs)
        def _check((rc,out,err)):
            self.failUnlessEqual(err, "", verb)
            self.failUnlessEqual(rc, 0, verb)
            return out
        d.addCallback(_check)
        return d

    def test_arg_parsing(self):
        self.basedir = "consolidate/Consolidate/arg_parsing"
        self.set_up_grid(num_clients=1, num_servers=1)
        co = debug.ConsolidateOptions()
        co.parseOptions(["--node-directory", self.get_clientdir(),
                         "--dbfile", "foo.db", "--backupfile", "backup", "--really",
                         "URI:DIR2:foo"])
        self.failUnlessEqual(co["dbfile"], "foo.db")
        self.failUnlessEqual(co["backupfile"], "backup")
        self.failUnless(co["really"])
        self.failUnlessEqual(co.where, "URI:DIR2:foo")

    def OFF_test_basic(self):
        # rename this method to enable the test. I've disabled it because, in
        # my opinion:
        #
        #  1: 'tahoe debug consolidate' is useful enough to include in trunk,
        #     but not useful enough justify a lot of compatibility effort or
        #     extra test time
        #  2: it requires sqlite3; I did not bother to make it work with
        #     pysqlite, nor did I bother making it fail gracefully when
        #     sqlite3 is not available
        #  3: this test takes 30 seconds to run on my workstation, and it likely
        #     to take several minutes on the old slow dapper buildslave
        #  4: I don't want other folks to see a SkipTest and wonder "oh no, what
        #     did I do wrong to not allow this test to run"
        #
        # These may not be strong arguments: I welcome feedback. In particular,
        # this command may be more suitable for a plugin of some sort, if we
        # had plugins of some sort. -warner 12-Mar-09

        self.basedir = "consolidate/Consolidate/basic"
        self.set_up_grid(num_clients=1)

        fileutil.make_dirs(os.path.join(self.basedir, "home/Backups/nonsystem"))
        fileutil.make_dirs(os.path.join(self.basedir, "home/Backups/fluxx/Latest"))
        self.writeto(os.path.join(self.basedir,
                                  "home/Backups/fluxx/Archives/nondir"),
                     "not a directory: ignore me")

        # set up a number of non-shared "snapshots"
        for i in range(1,8):
            self.writeto_snapshot(i, "parent/README", "README")
            self.writeto_snapshot(i, "parent/foo.txt", "foo")
            self.writeto_snapshot(i, "parent/subdir1/bar.txt", "bar")
            self.writeto_snapshot(i, "parent/subdir1/baz.txt", "baz")
            self.writeto_snapshot(i, "parent/subdir2/yoy.txt", "yoy")
            self.writeto_snapshot(i, "parent/subdir2/hola.txt", "hola")

            if i >= 1:
                pass # initial snapshot
            if i >= 2:
                pass # second snapshot: same as the first
            if i >= 3:
                # modify a file
                self.writeto_snapshot(i, "parent/foo.txt", "FOOF!")
            if i >= 4:
                # foo.txt goes back to normal
                self.writeto_snapshot(i, "parent/foo.txt", "foo")
            if i >= 5:
                # new file
                self.writeto_snapshot(i, "parent/subdir1/new.txt", "new")
            if i >= 6:
                # copy parent/subdir1 to parent/subdir2/copy1
                self.writeto_snapshot(i, "parent/subdir2/copy1/bar.txt", "bar")
                self.writeto_snapshot(i, "parent/subdir2/copy1/baz.txt", "baz")
                self.writeto_snapshot(i, "parent/subdir2/copy1/new.txt", "new")
            if i >= 7:
                # the last snapshot shall remain untouched
                pass

        # now copy the whole thing into tahoe
        d = self.do_cli_good("create-alias", "tahoe")
        d.addCallback(lambda ign:
                      self.do_cli_good("cp", "-r",
                                       os.path.join(self.basedir, "home/Backups"),
                                       "tahoe:Backups"))
        def _copied(res):
            rootcap = get_aliases(self.get_clientdir())["tahoe"]
            # now scan the initial directory structure
            n = self.g.clients[0].create_node_from_uri(rootcap)
            return n.get_child_at_path([u"Backups", u"fluxx", u"Archives"])
        d.addCallback(_copied)
        self.nodes = {}
        self.caps = {}
        def stash(node, name):
            self.nodes[name] = node
            self.caps[name] = node.get_uri()
            return node
        d.addCallback(stash, "Archives")
        self.manifests = {}
        def stash_manifest(manifest, which):
            self.manifests[which] = dict(manifest)
        d.addCallback(lambda ignored: self.build_manifest(self.nodes["Archives"]))
        d.addCallback(stash_manifest, "start")
        def c(n):
            pieces = n.split("-")
            which = "finish"
            if len(pieces) == 3:
                which = pieces[-1]
            sn = int(pieces[0])
            name = pieces[1]
            path = [u"2009-03-%02d 01.01.01" % sn]
            path.extend( {"b": [],
                          "bp": [u"parent"],
                          "bps1": [u"parent", u"subdir1"],
                          "bps2": [u"parent", u"subdir2"],
                          "bps2c1": [u"parent", u"subdir2", u"copy1"],
                          }[name] )
            return self.manifests[which][tuple(path)]

        dbfile = os.path.join(self.basedir, "dirhash.db")
        backupfile = os.path.join(self.basedir, "backup.pickle")

        d.addCallback(lambda ign:
                      self.do_cli_good("debug", "consolidate",
                                       "--dbfile", dbfile,
                                       "--backupfile", backupfile,
                                       "tahoe:"))
        def _check_consolidate_output1(out):
            lines = out.splitlines()
            last = lines[-1]
            self.failUnlessEqual(last.strip(),
                                 "system done, "
                                 "7 dirs created, 2 used as-is, 13 reused")
            self.failUnless(os.path.exists(dbfile))
            self.failUnless(os.path.exists(backupfile))
            backup = pickle.load(open(backupfile, "rb"))
            self.failUnless(u"fluxx" in backup["systems"])
            self.failUnless(u"fluxx" in backup["archives"])
            adata = backup["archives"]["fluxx"]
            kids = adata[u"children"]
            self.failUnlessEqual(str(kids[u"2009-03-01 01.01.01"][1][u"rw_uri"]),
                                 c("1-b-start"))
        d.addCallback(_check_consolidate_output1)
        d.addCallback(lambda ign:
                      self.do_cli_good("debug", "consolidate",
                                       "--dbfile", dbfile,
                                       "--backupfile", backupfile,
                                       "--really", "tahoe:"))
        def _check_consolidate_output2(out):
            lines = out.splitlines()
            last = lines[-1]
            self.failUnlessEqual(last.strip(),
                                 "system done, "
                                 "0 dirs created, 0 used as-is, 0 reused")
        d.addCallback(_check_consolidate_output2)

        d.addCallback(lambda ignored: self.build_manifest(self.nodes["Archives"]))
        d.addCallback(stash_manifest, "finish")

        def check_consolidation(ignored):
            #for which in ("finish",):
            #    for path in sorted(self.manifests[which].keys()):
            #        print "%s %s %s" % (which, "/".join(path),
            #                            self.manifests[which][path])

            # last snapshot should be untouched
            self.failUnlessEqual(c("7-b"), c("7-b-start"))

            # first snapshot should be a readonly form of the original
            from allmydata.scripts.tahoe_backup import readonly
            self.failUnlessEqual(c("1-b-finish"), readonly(c("1-b-start")))
            self.failUnlessEqual(c("1-bp-finish"), readonly(c("1-bp-start")))
            self.failUnlessEqual(c("1-bps1-finish"), readonly(c("1-bps1-start")))
            self.failUnlessEqual(c("1-bps2-finish"), readonly(c("1-bps2-start")))

            # new directories should be different than the old ones
            self.failIfEqual(c("1-b"), c("1-b-start"))
            self.failIfEqual(c("1-bp"), c("1-bp-start"))
            self.failIfEqual(c("1-bps1"), c("1-bps1-start"))
            self.failIfEqual(c("1-bps2"), c("1-bps2-start"))
            self.failIfEqual(c("2-b"), c("2-b-start"))
            self.failIfEqual(c("2-bp"), c("2-bp-start"))
            self.failIfEqual(c("2-bps1"), c("2-bps1-start"))
            self.failIfEqual(c("2-bps2"), c("2-bps2-start"))
            self.failIfEqual(c("3-b"), c("3-b-start"))
            self.failIfEqual(c("3-bp"), c("3-bp-start"))
            self.failIfEqual(c("3-bps1"), c("3-bps1-start"))
            self.failIfEqual(c("3-bps2"), c("3-bps2-start"))
            self.failIfEqual(c("4-b"), c("4-b-start"))
            self.failIfEqual(c("4-bp"), c("4-bp-start"))
            self.failIfEqual(c("4-bps1"), c("4-bps1-start"))
            self.failIfEqual(c("4-bps2"), c("4-bps2-start"))
            self.failIfEqual(c("5-b"), c("5-b-start"))
            self.failIfEqual(c("5-bp"), c("5-bp-start"))
            self.failIfEqual(c("5-bps1"), c("5-bps1-start"))
            self.failIfEqual(c("5-bps2"), c("5-bps2-start"))

            # snapshot 1 and snapshot 2 should be identical
            self.failUnlessEqual(c("2-b"), c("1-b"))

            # snapshot 3 modified a file underneath parent/
            self.failIfEqual(c("3-b"), c("2-b")) # 3 modified a file
            self.failIfEqual(c("3-bp"), c("2-bp"))
            # but the subdirs are the same
            self.failUnlessEqual(c("3-bps1"), c("2-bps1"))
            self.failUnlessEqual(c("3-bps2"), c("2-bps2"))

            # snapshot 4 should be the same as 2
            self.failUnlessEqual(c("4-b"), c("2-b"))
            self.failUnlessEqual(c("4-bp"), c("2-bp"))
            self.failUnlessEqual(c("4-bps1"), c("2-bps1"))
            self.failUnlessEqual(c("4-bps2"), c("2-bps2"))

            # snapshot 5 added a file under subdir1
            self.failIfEqual(c("5-b"), c("4-b"))
            self.failIfEqual(c("5-bp"), c("4-bp"))
            self.failIfEqual(c("5-bps1"), c("4-bps1"))
            self.failUnlessEqual(c("5-bps2"), c("4-bps2"))

            # snapshot 6 copied a directory-it should be shared
            self.failIfEqual(c("6-b"), c("5-b"))
            self.failIfEqual(c("6-bp"), c("5-bp"))
            self.failUnlessEqual(c("6-bps1"), c("5-bps1"))
            self.failIfEqual(c("6-bps2"), c("5-bps2"))
            self.failUnlessEqual(c("6-bps2c1"), c("6-bps1"))

        d.addCallback(check_consolidation)

        return d

    def build_manifest(self, root):
        # like dirnode.build_manifest, but this one doesn't skip duplicate
        # nodes (i.e. it is not cycle-resistant).
        manifest = []
        manifest.append( ( (), root.get_uri() ) )
        d = self.manifest_of(None, root, manifest, () )
        d.addCallback(lambda ign: manifest)
        return d

    def manifest_of(self, ignored, dirnode, manifest, path):
        d = dirnode.list()
        def _got_children(children):
            d = defer.succeed(None)
            for name, (child, metadata) in children.iteritems():
                childpath = path + (name,)
                manifest.append( (childpath, child.get_uri()) )
                if IDirectoryNode.providedBy(child):
                    d.addCallback(self.manifest_of, child, manifest, childpath)
            return d
        d.addCallback(_got_children)
        return d