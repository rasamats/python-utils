# ==================================================================================
class RunError(Exception):
    def __init__(self, ex_info, errno = None, cmd = None, trace = None):
        self.ex_info = ex_info
        self.errno = errno
        self.cmd = cmd
        self.trace = trace

    def __str__(self):
        return self.ex_info

# ==================================================================================
def run(cmd, **kwargs):

    # Read our own arguments
    verbose = kwargs.pop('verbose', False)
    raise_exception = kwargs.pop('raise_exception', True)
    exit_on_error = kwargs.pop('exit_on_error', False)

    # If stderr is not there, redirect it to stdout
    if kwargs.get('stderr', None) == None:
        kwargs['stderr'] = subprocess.STDOUT

    exception_output = None

    # One more goodie: if cmd is a string, split it here
    if type(cmd) is not list:
        cmd = shlex.split(cmd)

    if verbose:
        logging.info("Running command '%s', cwd '%s'", " ".join(cmd), kwargs.get('cwd', None))

    try:
        # Run the command
        stdout = kwargs.get('stdout', None)
        if stdout:
            output = ""
            # Print the stdout as it arrives
            p = subprocess.Popen(cmd, bufsize = 1, universal_newlines = True, **kwargs)
            if stdout == subprocess.PIPE:
                for line in iter(p.stdout.readline, ''):
                    line = line.replace('\r', '').replace('\n', '')
                    output += line
                    print line
                    sys.stdout.flush()
            # Wait until the command is done
            errno = p.wait()
        else:
            kwargs.pop('stdout', None)
            # Run the command, wait until it's done, collect the output
            output = subprocess.check_output(cmd, **kwargs)
            errno = 0

    except subprocess.CalledProcessError, e:
        # If this is a valid command failing to execute check_output()
        # will raise CalledProcessError
        #
        errno = e.returncode
        output = e.output

    except Exception, e:
        # In any other case (e.g. command not found or is not executable):
        errno = sys.exc_info()[0]
        output = str(e)
        exception_output = output

    except KeyboardInterrupt, e:
        errno = sys.exc_info()[0]
        output = str(e)

    finally:
        # We never need the trailing '\n', so get rid of it here
        output = output.rstrip()

        if errno:
            if exit_on_error:
                if stdout == subprocess.PIPE:
                    if exception_output:
                        logging.error(exception_output)
                else:
                    logging.error(output)
                logging.error("Exit %s with error: %s", os.path.basename(sys.argv[0]), errno)
                exit(errno)
            if raise_exception:
                trace = " ".join(list(traceback.format_exception(*sys.exc_info())))
                cmd = " ".join(cmd)
                raise RunError(output, errno = errno, cmd = cmd, trace = trace)

    return output
# ==================================================================================
# A subprocess wrapper returning a trio of (result, stdout, stderror)
# Examples:
#  result, stdout, stderr = run2 ("/path/to/my/utility -a arg")

def run2(cmd, cwd='.', verbose=False, shell=False, split = False):
    if verbose is True:
        if cwd is not ".":
            print "cd %s &&" % cwd,
        print cmd

    # Shell commands are parsed by shell as a string
    if not split and not shell:
        cmd = shlex.split(cmd)

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, shell=shell)

    stdout, stderr = map(lambda s: s.rstrip('\n'), p.communicate())
    error = p.wait()

    return (error, stdout, stderr)

# ==================================================================================
class Runner():

    def __init__(self, cwd, exit_on_error = False, verbose = False, stdout = None):

        self.cwd = cwd
        self.kwargs = {}
        self.kwargs['exit_on_error'] = exit_on_error
        self.kwargs['verbose'] = verbose
        if stdout:
            self.kwargs['stdout'] = stdout

    @classmethod
    def varname(cls, arg):

        return [ k for k, v in locals().iteritems() if v is arg][0]

    def run(self, cmd, **kwargs):

        if type(cmd) is not list:
            cmd = shlex.split(cmd)

        pass_kwargs = copy.deepcopy(self.kwargs)

        for arg, val in kwargs.iteritems():
            if arg == 'exit_on_error' or arg == 'stdout' or arg == 'raise_exception':
                # ae want these to be passed down to utils.run()
                pass_kwargs[arg] = val
            elif val:
                cmd.append("--%s" % arg)

        return run(cmd, cwd=self.cwd, **pass_kwargs)

# ==================================================================================
# Go over the given entries and spawn the given function in parallel threads
def forall(jobs, entries, func, *args, **kwargs):

    def run_func(func, entry, *args, **kwargs):
        try:
            errno = func(entry, *args, **kwargs)
        except:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            traceback.print_exception(exc_type, exc_value, exc_traceback)
            errno = sys.exc_info()[0]
        finally:
            return errno

    class Worker(threading.Thread):

        def __init__(self, event, entries, func, *args, **kwargs):

            threading.Thread.__init__(self)
            self.event = event
            self.entries = entries
            self.func = func
            self.args = args
            self.kwargs = kwargs
            self.errno = 0
            self.progress_bar = self.kwargs.pop('progress_bar', None)

        def run(self):

            for entry in self.entries:
                if self.event.is_set():
                    break
                self.errno = run_func(func, entry, *args, **self.kwargs)
                if self.errno:
                    self.event.set()
                    if self.progress_bar:
                        self.progress_bar.stop(status = "Error")
                    return self.errno
                if self.progress_bar:
                    self.progress_bar.add()

        def join(self, timeout = None):
            threading.Thread.join(self, timeout = timeout)
            return self.errno

    jobs = int(jobs)

    num_entries = len(entries)
    if not num_entries:
        return 0

    progress_bar_name = kwargs.pop('progress_bar_name', None)
    if progress_bar_name:
        progress_bar = ProgressBar(name = progress_bar_name, items = num_entries)
        kwargs['progress_bar'] = progress_bar
    else:
        progress_bar = None

    # We may run just as a simple loop, if no jobs are given
    if jobs == 0:
        for entry in entries:
            errno = run_func(func, entry, *args, **kwargs)
            if errno:
                if progress_bar:
                    progress_bar.stop(status = "Error")
                return errno
            if progress_bar:
                progress_bar.add()
        return 0

    event = threading.Event()

    chunk_size = (num_entries/jobs)
    if num_entries < jobs:
        chunk_size = 1

    threads = []
    for thread_id in range(0, jobs):
        # Divide our entries list into chunks
        start = thread_id * chunk_size
        # Make sure the last chunk is big enough to cover the rest of the entries
        if thread_id == jobs -1:
            stop = None
        else:
            stop = (thread_id + 1) * chunk_size
        chunk = list(entries)[start:stop]
        if not chunk:
            # we are done
            break
        # Create and start a new thread
        thread = Worker(event, chunk, func, *args, **kwargs)
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete and see if there were any errors
    errors = 0
    for thread in threads:
        if thread.join():
            errors += 1

    return errors
