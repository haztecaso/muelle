# Response headers as an attrset (name -> value, null drops one), shared by
# the nginx and static modules.
lib: {
  # every vhost muelle declares; each one can be overridden on its own
  defaults = {
    # without includeSubDomains or preload: the apex may not be yours
    Strict-Transport-Security = "max-age=31536000";
    X-Content-Type-Options = "nosniff";
    Referrer-Policy = "strict-origin-when-cross-origin";
    # no framing by other sites (clickjacking); X-Frame-Options for old browsers
    X-Frame-Options = "DENY";
    Content-Security-Policy = "frame-ancestors 'none'";
  };

  render =
    headers:
    lib.concatStrings (
      lib.mapAttrsToList (
        name: value:
        lib.optionalString (value != null) ''
          add_header ${name} "${lib.escape [ "\\" "\"" ] value}" always;
        ''
      ) headers
    );
}
