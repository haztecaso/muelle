# Migrating an existing site

Seed the first release from the current docroot without copying data
(hard links, same filesystem), then set `domain` (or point your vhost at
`.root`):

```sh
site=mysite; old=/var/www/mysite
base=/var/www/muelle/$site/prod
sudo install -d -o muelle -g muelle "$base/releases"
sudo cp -al "$old" "$base/releases/initial"
sudo chown -R muelle:muelle "$base/releases/initial"   # also changes owner of $old's inodes
sudo -u muelle ln -sfn releases/initial "$base/current"
```

Content that is not produced by the site build (archives, uploads)
disappears on the next deploy; serve it from a separate path.
