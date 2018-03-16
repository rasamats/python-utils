import sys
sys.dont_write_bytecode = False
import logging
import os
import shutil
import errno
import re
import shlex
from tempfile import NamedTemporaryFile

import commit
import utils
from revision import Revision, revision_branch_name

# ============================================================================
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

GITIGNORE = ".gitignore"
GITATTRIBUTES = ".gitattributes"

class GitError(utils.RunError):
    pass

# ==================================================================================
# Examples:
#  git = Git("/path/to/repo")
#  git.clone("evogit:sandbox")
#  git.run("pull origin master")
#  git.pull("origin master")

class Git:

    def __init__(self, path='.', remote_repo = None, verbose = False, raise_exception = True):

        self.remote_repo = remote_repo
        self.path = path
        self.name = os.path.dirname(path)
        self.verbose = verbose
        self.raise_exception = raise_exception
        self.errno = 0

    # All git commands (unless overloaded) should just appear as methods here
    def __getattr__(self, name):

        if name.startswith("__") and name.endswith("__"):
            raise AttributeError

        # Git commands containing "-" will have "_" instead, e.g. git.ls_remote()
        name = name.replace('_', '-')

        return lambda *args, **kwargs: self.run("%s %s" % (name, " ".join(args)), **kwargs)

    def __str__(self):

        if self.isrepo():
            return "git repo: path: %s topic branch: %s revision: %s" % \
                    (self.path, self.topic_branch(), self.rev(self.current_revision()))
        else:
            return "Not a git repo: path: %s" % self.path


        return output

    def clone(self, repository, bare = False, upstream_branch = None, **kwargs):

        kwargs['cwd'] = kwargs.get('cwd', ".")

        bare_arg = "--bare" if bare else ""
        branch_arg = "--branch %s" % upstream_branch if upstream_branch else ""

        # See if we need to raise an exception
        return self.run("clone %s %s %s %s" %
                        (repository, self.path, bare_arg, branch_arg),
                         **kwargs)

    def isrepo(self, rev_parse = True):

        if not os.path.isdir(self.path):
            # working directory is missing
            return False

        if not rev_parse:
            return True

        # See if there is a .git directory here
        if self.run("rev-parse --is-inside-work-tree", verbose = False, raise_exception = False) == "true":
            # Working git directory
            return True

        # See if this is a bare repo
        if self.run("rev-parse --is-bare-repository", verbose = False, raise_exception = False) == "true":
            # Bare repo
            return True

        return False

    def delete_branch(self, branch_name, remote = "origin"):

        if not self.branch_exists(branch_name, remote):
            return

        if remote:
            self.push("%s :refs/heads/%s" % (remote, branch_name))
        else:
            self.branch("-d %s" % branch_name)

    def branch_exists(self, branch_name, remote = "origin"):

        if remote:
            branch_name = "remotes/%s/%s" % (remote, branch_name)

        try:
            self.show_branch(branch_name, verbose = False)
            return True
       except GitError:
            return False

    def path_exists(self, path, branch = None, fetch = False):

        try:
            revision = self.latest_revision (upstream_branch = branch, fetch = fetch)
            self.cat_file("-e %s:%s" % (revision, path), verbose = False)
            return True

        except GitError:
            return False

    def get_remote(self, index = 0):

        topic_branch = self.topic_branch()
        return self.config('--get-all branch.%s.remote' % topic_branch).rsplit()[index].rstrip()

    # Overload defautl ls-remote with one that swallows stderror
    def ls_remote(self, *args, **kwargs):

        kwargs['stderr'] = open(os.devnull, 'w')
        return self.run("ls-remote %s" % ("".join(args)), **kwargs)

    # Create a branch locally, push it to the default remote and set it as upstream.
    def create_branch(self, branch, force = False, push = True, set_as_upstream = False):

        force_flag = "-f" if force else ""
        remote = self.remote()

        self.branch("%s %s" % (force_flag, branch))

        if push:
            logging.debug("Creating branch %s in %s", branch, remote)
            self.push("%s %s:%s" % (remote, branch, branch))
        else:
            logging.debug("Skip pushing branch %s to %s", branch, remote)

        if set_as_upstream:
            self.config("branch.%s.merge %s" % (self.topic_branch(), branch))

    # Tag a repository and push the tag
    def tag_repo(self, tag, args = '', push=True):

        self.tag("%s %s" % (tag, args))
        if push:
            self.push("%s %s" % (self.remote(), tag))

    def topic_branch(self, try_rebase_merge_dir = False):

        branch = self.rev_parse("--abbrev-ref HEAD")

        if branch != "HEAD":
            return branch

        if try_rebase_merge_dir:
            rebase_merge_dir = os.path.join(self.path, ".git", "rebase-merge")
            if os.path.isdir(rebase_merge_dir):
                head_name_file = os.path.join(rebase_merge_dir, "head-name")
                try:
                    with open (head_name_file) as fd:
                        head_name = fd.read().rstrip("\n")
                        return head_name.replace("refs/heads/","")
                except IOError, e:
                    if e.errno == errno.ENOENT:
                        raise GitError("Can not find rebase-merge directory. No rebase in progress?",
                                        errno = errno.ENOENT)

        raise GitError("Detached head in %s" % self.path, errno = errno.ENOENT)

    def current_revision(self, ref = 'HEAD'):

        return self.rev_parse(ref)

    def upstream_branch(self, branch = None):

        topic_branch = branch or self.topic_branch()
       try:
            upstream_branch = self.config("branch.%s.merge" % topic_branch)

        except GitError:
            logging.error("No upstream branch for topic %s, are you in the middle of rebase?", topic_branch)
            raise

        return upstream_branch.replace("refs/heads/","")

    def remote_tags(self, regex, fetch = True):

        if fetch:
            self.fetch()

        # The output of ls-remote looks like this:
        # 46dba5752ea0308cc204c3ddbf0cb04b3fe6f809        refs/tags/master/pub-20141207.2
        # 21415606422f14de340c9978f56a8cf18ffd356e        refs/tags/devel/pub-20141210.3
        # ...
        try:
            output = self.ls_remote("%s refs/tags/*" % (self.remote_repo or '.'), verbose = False)

            # We need the right part of the right column (after refs/tags)
            tags = map(lambda s: s.split('\t')[1].split("refs/tags/")[1], output.split('\n'))

            if regex:
                return filter(lambda s:re.match(regex, s), tags)
            else:
                return tags

        except:
            return []

    def latest_tag(self, fetch = True):

        if fetch:
            self.fetch("-t")

        return self.describe("--tags --abbrev=0 %s" % self.upstream_branch())


    def remote_revision(self, revision = 'HEAD', upstream_branch = None, fetch = True):

        if fetch:
            self.fetch()

        upstream_branch = upstream_branch or self.upstream_branch()

        if revision.startswith("HEAD"):
            revision = revision.replace("HEAD", os.path.join(self.get_remote(), upstream_branch))

        return self.rev(revision)

    def fast_remote_revision(self, remote, branch, fetch = False):

        if fetch:
            self.fetch()

        return self.rev_parse("%s/%s" % (remote, branch))

    def latest_revision(self, upstream_branch = None, fetch = True):

        return self.remote_revision(upstream_branch = upstream_branch, fetch = fetch)

    def remote_heads(self, pattern = "", remote = None, fetch = True):

        if not remote:
            remote = self.get_remote()

        if fetch:
            self.fetch(remote)

        output = self.ls_remote("%s --heads %s %s" % (self.remote_repo or '', remote, pattern))
        if not output:
            return None

        # We need the right part of the right column (after refs/heads)
        return map(lambda s: s.split('\t')[1].split("refs/heads/")[1], output.split('\n'))

    def real_branches(self, remote = None, fetch = True):

        if not remote:
            remote = self.get_remote()

        if fetch:
            self.fetch(remote)

        output = self.branch("--remote --list \"%s/*\" --no-color" % remote)
        if not output:
            return None

        # Output will look like this:
        #   HEAD -> origin/master
        #   origin/FOO_BRANCH
        #   origin/pub-20150206.6
        #   origin/v/master/0.0.444

        # Now make a list of branch names: remove extra spaces, dereferences
        # and remote name from the path. The list from above becomes:
        # master
        # FOO_BRANCH
        # pub-20150206.6
        # v/master/0.0.444
        branch_names = map(lambda s: re.sub(".*-> ", "", s.split("  ")[1]).split("%s/" % remote)[1], output.split('\n'))

        # remove special branches, in example above leave only:
        # master
        # FOO_BRANCH
        regex = re.compile("^v/.*|^pub-\d+\.\d+$|^build-\d+\.\d+$")
        real_branches = filter(lambda each:not re.match(regex, each), branch_names) + ['master']

        return list(set(real_branches))


    def branches_with_revision(self, revision, pattern = '', strip_remote = True):

        try:
            output = self.branch("--contains %s --all --list --no-color" % revision, verbose = False)
        except GitError:
            return []

        # Output will look like this:
        # * somebranch
        #    master
        #    remotes/origin/HEAD -> origin/master
        #    remotes/origin/master
        # We need to remove two first character, take only remote branches and trim everything
        # but the branch name
        remote_str = "remotes/%s/" % self.get_remote()
        regex = re.compile("%s%s[^\s]*$" % (remote_str, pattern)) # take lines with no whitespace
        branches = filter(lambda each:re.match(regex, each), map(lambda each:each[2:], output.splitlines()))
        return map(lambda each:each.replace(remote_str,'') if strip_remote else each, branches)


    def commit_is_merged(self, revision):

        try:
            output = self.branch("--contains %s --remote --list --no-color" % revision, verbose = False)
            if output:
                # Commit is reachable in remote refs/heads
                return True
            # Commit sits in refs/changes and not in refs/heads

        except GitError:
            # no such revision in remote branches? this is a local change!
            pass

    def get_m_branch(self):

        m_ref = "refs/remotes/m"
        try:
            output = self.ls_remote(". %s*" % m_ref)
            if output:
                return output.split()[1].replace("%s/" % m_ref, "", 1)
        except GitError:
            pass
    def first_repo_revision(self, revision, branch = None):

        try:
            ref_remotes_pattern = "refs/remotes/%s/" % self.get_remote()
            branch_pattern = "%s/*" % branch if branch else "*"
            pattern = "%sv/%s" % (ref_remotes_pattern, branch_pattern)

            # git sorts alphanumerically, it will bring 0.0.10 before 0.0.9
            # so we need all matching refs for ourselves to sort
            output = self.for_each_ref("%s --points-at %s --format '%%(refname)'" % (pattern, revision))
            # Output will look like this:
            #
            # refs/remotes/origin/v/master/0.0.10
            # refs/remotes/origin/v/master/0.0.11
            # refs/remotes/origin/v/master/0.0.9
            if output:
                revs = [ Revision(x.replace(ref_remotes_pattern, "")) for x in output.splitlines() ]
                return revision_branch_name(min(revs))
        except GitError:
            pass

    def last_repo_revision(self, branch = None, fetch = True):

        # Use repo's upstream branch by default
        branch = branch or self.get_m_branch()
        if not branch:
            return None

        # Fetch, if needed
        if fetch:
            self.fetch()

        # Get repo's default remote
        remote = self.get_remote()
        ref_remotes_pattern = "refs/remotes/%s/" % remote
        remote_branch = "%s/%s" % (remote, branch)

        # Go over all remote commits - from the tip all the way back to our parent
        # and see if they point to a published revision
        # (we could look deeper, but it is safer to stop somewhere...)
        try:
            parent = self.merge_base("HEAD %s" % remote_branch)
            revisions = self.rev_list("%s~..%s" % (parent, remote_branch)).split()
        except GitError:
            # Our parent is the only revision?
            pass
        if not revisions:
            revisions = [parent]

        for revision in revisions:
            try:
                output = self.for_each_ref("%sv/%s/ --points-at %s --format '%%(refname)'" % (ref_remotes_pattern, branch, revision))
                if output:
                    # Output will look like this:
                    #
                    # refs/remotes/origin/v/master/0.0.10
                    # refs/remotes/origin/v/master/0.0.11
                    # refs/remotes/origin/v/master/0.0.9
                    #
                    # Now we just need the latest, largest number
                    revs = [ Revision(x.replace(ref_remotes_pattern, "")) for x in output.splitlines() ]
                    return revision_branch_name(max(revs))
                # else - go one revision earlier
            except GitError:
                pass

    def rev(self, revision = "HEAD", show_tag = False):

        try:
            output = self.show_ref("--abbrev %s" % revision, verbose = False)
            # Revision is a tag or a branch
            if show_tag:
                return output
            # Get to the actual commit, revision will be a hash
            try:
                revision = self.rev_list("-1 %s" % revision, verbose = False)
            except GitError:
                return None
        except:
            pass

        # Revision is a hash, return its short form
        try:
            return self.rev_parse("--short %s" % revision, verbose = False)

        except GitError:
            return None

    '''
    Dumps out something like:
    v/master/pub-20160227.2 v/master/LATEST_SMOKE <7767d28>
    '''
    def dump_revision(self, revision, fetch = False):

        if fetch:
            self.fetch()

        output = ""

        abbrev_revision = self.rev_parse(" --short %s" % self.rev_list("-1 %s" % revision))

        for tag in reversed(self.tag(" --points-at %s" % abbrev_revision).split('\n')):
            output += "%s " % tag

        # Add revision's hash
        output += "<%s>" % abbrev_revision

        return output

    def tracks_published(self, revision = None, fetch = True):

        if fetch:
            self.fetch()

        if not revision:
            revision = self.current_revision()

        pub_tags = self.tag("-l --contains %s v/*/pub-*" % self.rev(revision))
        if pub_tags:
            return pub_tags.split('\n')[0]

    def uncommitted_changes(self):
        try:
            return self.diff ("HEAD", verbose = False).splitlines()

        except GitError:
            return None

    def committed_changes(self, reverse = True, fetch = False):

        reverse_arg = "--reverse" if reverse else ""
        output = self.rev_list("--first-parent %s %s..HEAD" % (reverse_arg, self.latest_revision(fetch = fetch)))
        if output:
            return output.splitlines()
        return None

    def get_parent(self, upstream_branch = None, revision = None, fetch = False):

        if fetch:
            self.fetch()

        branch = upstream_branch or self.upstream_branch()
        revision = revision or self.current_revision(branch)
    def set_push_and_fetch(self, push_url = None):

        remote = self.get_remote()

        if not push_url:
            review_url = self.config("remote.%s.review" % remote, raise_exception = False)
            project_name = self.config("remote.%s.projectname" % remote, raise_exception = False)
            if review_url and project_name:
                push_url = "%s/%s" % (review_url, project_name)

        self.config("--unset remote.%s.pushurl" % remote, raise_exception = False)
        if push_url:
            self.config("remote.%s.pushurl %s" % (remote, push_url))

        logging.debug("Setup refspec to fetch the notes and jnpr specific referencess ...")
        self.config("--unset-all remote.%s.fetch" % remote)
        self.config("--add remote.%s.fetch +refs/heads/*:refs/remotes/origin/*" % remote)
        self.config("--add remote.%s.fetch +refs/notes/*:refs/notes/*" % remote)
        self.config("--add remote.%s.fetch +refs/jnpr/*:refs/jnpr/*" % remote)

    def extract_path(self, extract_path, stdout = None):

        logging.info("Extracting %s, this can take a while", extract_path)

        output = self.filter_branch("--tag-name-filter cat --subdirectory-filter %s -- --all" % extract_path, stdout = stdout)

        if output:
            # Remove dangling references, they appear in output as:
            # "WARNING: Ref 'refs/tags/v0.0.91' is unchanged"
            start_marker = "WARNING: Ref '"
            end_marker = "' is unchanged"
            for line in output.split("\n"):
                if line.startswith(start_marker) and line.endswith(end_marker):
                    ref = line.replace(start_marker, "").replace(end_marker, "")
                    # If we remove the reference that tracks remote HEAD,
                    # we won't be able to gc or repack later, so leave it alone
                    remote_head_ref = "refs/remotes/%s/%s" % (self.get_remote(), self.topic_branch())
                    logging.debug("Will not remove %s", remote_head_ref)
                    if ref != remote_head_ref:
                        self.update_ref("-d %s" % ref)

        # Remove the namespace where the original commits are stored
        shutil.rmtree(self.path + "/.git/refs/original", ignore_errors = True)

        # Remove reflog, prune garbage collection and repack
        self.reflog("expire --verbose --expire=0 --all", stdout = stdout)
        self.gc("--prune=0", stdout = stdout)
        self.repack("-ad", stdout = stdout)

    def rebase_repo(self, upstream_branch = None, revision = None,
                    fetch = True, verbose = False, silent = False):

        log = logging.debug if silent else logging.info

        # First, fetch heads and tags
        if fetch:
            self.fetch()
            self.fetch("-t")

        ''' Let's say we have:

        A -- B -- C    master
           \
             D -- E -- F topic

        H -- K -- L    DEV_BRANCH

        (topic tracks master, old graft point is A)

        And we want to rebase the repo to become:
        A -- B -- C    master

                  D -- E -- F  topic
                /
        H -- K -- L    DEV_BRANCH

        (topic tracks DEV_BRANCH, new graft point is K)

        This function will be called with upstream_branch = DEV_BRANCH, revision = K

        ... we need to run:

        git rebase --onto K(new_graft) A(old_graft) F(topic's HEAD)

        '''

        old_upstream_branch = self.upstream_branch()
        new_upstream_branch = upstream_branch or old_upstream_branch
        new_graft = self.remote_revision(revision = revision or "HEAD",
                                         upstream_branch = new_upstream_branch,
                                         fetch = False)
        if not new_graft:
            if revision:
                logging.error("Unknown revision %s", revision)
            elif upstream_branch:
                logging.error("Unknown branch %s", upstream_branch)
            exit(errno.ENOENT)

        HEAD = self.rev(self.topic_branch())
        old_graft = self.rev(self.get_parent(old_upstream_branch, HEAD))

        try:
            if HEAD != new_graft:
                log("Rebase %s onto %s",
                     os.path.basename(os.path.abspath(self.path)),
                     self.dump_revision(new_graft))

                if HEAD != old_graft:
                    log("Replay commits: <%s>..<%s>", old_graft, HEAD)

                self.rebase("--preserve-merges --onto %s %s %s" % (new_graft, old_graft, self.topic_branch()), verbose = verbose)

        except GitError:
            raise

        finally:
            # Success or failure, we need to track the given branch
            if new_upstream_branch != old_upstream_branch:
                log("Switch to branch %s", new_upstream_branch)
                self.config("--replace-all branch.%s.merge %s" % (self.topic_branch(), new_upstream_branch))

    def file_is_dirty (self, fname):

        diff_cmd = "--diff-filter=ACMR --name-only "
        return self.diff(diff_cmd + fname) == fname or \
               self.diff(diff_cmd + " --cached " + fname) == fname


    def merge_for_upload(self, *args, **kwargs):
        current_head = self.rev_parse('HEAD')
        try:
            self.merge("--no-ff %s" % (" ".join(args)), **kwargs)
        except:
            raise
        else:
            new_head = self.rev_parse('HEAD')
            if not new_head == current_head:
                # New merge commit is created.
                commit_obj = commit.Commit(self, 'HEAD')
                change_id = commit_obj.change_id()
                if not change_id:
                    # Ammend the commit message to insert the change-id
                    commit_msg = commit_obj.get_commit_msg_only()
                    with NamedTemporaryFile('w', delete=False) as tmp_file:
                        tmp_file.write(commit_msg)
                        tmp_file.close()
                        tmp_file_name = tmp_file.name
                    self.commit("--amend --reset-author -F %s" % tmp_file_name)
                    os.remove(tmp_file_name)

    def conflicting_commits_authors(self, fname):
        authors = self.log("--merge --pretty=%%ae %s" % fname)
        return authors

    def get_merge_head(self):
        try:
            git_dir = self.rev_parse("--git-dir", verbose = False)
            with open(os.path.join(self.path, git_dir, "MERGE_HEAD"), "r") as fp:
                return fp.read()
        except (GitError, IOError):
            pass

    def upstream_gain(self, remote = None, upstream_branch = None, base_revision = "HEAD", fetch = False):

        if not remote:
            remote = self.get_remote()
        if not upstream_branch:
            upstream_branch = self.upstream_branch()

        if base_revision != "HEAD":
            base_revision = "%s/%s" % (remote, base_revision)
        remote_branch = "%s/%s" % (remote, upstream_branch)

        if fetch:
            self.fetch()

        try:
            # We are going to parse some remote revisions, it's time to fetch
            upstream_commit = self.rev_parse(remote_branch)
            return self.rev_list("%s..%s" % (base_revision, upstream_commit)).split()
        except GitError:
            # No such remote branch? No upstream gain for you!
            pass

def get_git_directory(path = os.getcwd()):

    # Get real path
    realpath = os.path.realpath(path)
    if os.path.isfile(realpath):
        realpath = os.path.dirname(realpath)

    try:
        return Git(realpath).rev_parse("--show-toplevel", verbose = False)

    except GitError, e:
        logging.warning("Failed git.rev-parse --show-toplevel in %s: %s", realpath, e)
        return None

def update_gitattributes(path, pattern, attribute):

    gitattributes = os.path.join(path, GITATTRIBUTES)

    with open(gitattributes, "a+") as fdesc:

        reg_pattern = "^%s\s+%s\s*$" % (pattern.replace("*","\*"), attribute)
        reg = re.compile(reg_pattern)

        fdesc.seek(0)

        for _idx, line in enumerate(fdesc):
            for _match in re.finditer(reg, line):
                return

        logging.debug("Updating %s", gitattributes)
        fdesc.write("%s %s\n" % (pattern, attribute))

                                                                                            
