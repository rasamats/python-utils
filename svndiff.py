def svn_diff_to_git(svn_branch, svn_root, svn_map_path, svn_diff):

    git_diff = "Index: %s\n" % svn_map_path

    if svn_branch == "trunk":
        branch_pattern = "(trunk\/)"
    else:
        branch_pattern = "(branches\/%s\/)" % svn_branch
    path_pattern = branch_pattern + "([^\s^@]*)"

    for line in svn_diff.splitlines(True):
        # We are looking for this line:
        # diff --git a/branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h b/branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h.new
        match = re.match(r"^(diff --git )(a\/)%s(\s)(b\/)%s$" % (path_pattern, path_pattern), line)
        if match:
            # match.groups() is:
            # ('diff --git ', 'a/', 'branches/DEV_COMMON_BRANCH/', 'junos/include/jnx/appid_api.h',
            #   ' ', 'b/', 'branches/DEV_COMMON_BRANCH/', 'junos/include/jnx/appid_api.h.new')
            git_diff += match.group(1) + match.group(2) + os.path.relpath(match.group(4), svn_map_root) + \
                        match.group(5) + match.group(6) + os.path.relpath(match.group(8), svn_map_root) + '\n'
            # The end result is:
            # diff --git a/jnx/appid_api.h b/jnx/appid_api.h.new
            continue

        # We are lookning for something like:
        # --- a/branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h.new  (revision 918415)
        # +++ /dev/null   (working copy)
        match = re.match(r"^(--- |\+\+\+ )(((a\/|b\/)%s)|/dev/null)(.*)$" % path_pattern, line)
        if match:
            if match.group(4):
                # match.groups() is:
                # ('--- ', 'a/branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h.new',
                #  'a/branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h.new',
                #  'a/', 'branches/DEV_COMMON_BRANCH/', 'junos/include/jnx/appid_api.h.new', '  (revision 918415)')
                fname = match.group(4) + os.path.relpath(match.group(6), svn_map_root)
            else:
                # match.groups() is:
                # ('+++ ', '/dev/null', None, None, None, None, '   (working copy)')
                fname = match.group(2)
            git_diff += match.group(1) + fname + '\n'
            continue

        # We are looking for something like:
        # deleted file mode 10644
        # new file mode 10644
        match = re.match(r"^deleted file mode(.*)$", line)
        if match:
            # The modes may be incompatible, let's skip this for now
            continue

        # We are looking for something like:
        # copy from branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h@918415
        # copy to branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h.new
        match = re.match(r"^(copy from |copy to )(%s)(@\d+)?" % path_pattern, line)
        if match:
            # match.groups is:
            # ('copy from ', 'branches/DEV_COMMON_BRANCH/junos/include/jnx/appid_api.h',
            #  'branches/DEV_COMMON_BRANCH/', 'junos/include/jnx/appid_api.h', '@918415')
            git_diff += match.group(1) + os.path.relpath(match.group(4), svn_map_root) + '\n'
            continue

        git_diff += line

    return git_diff
