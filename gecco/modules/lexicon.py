#========================================================================
#GECCO - Generic Enviroment for Context-Aware Correction of Orthography
# Maarten van Gompel, Wessel Stoop, Antal van den Bosch
# Centre for Language and Speech Technology
# Radboud University Nijmegen
#
# Sponsored by Revisely (http://revise.ly)
#
# Licensed under the GNU Public License v3
#
#=======================================================================

import sys
import os
import json
from pynlpl.formats import folia
from pynlpl.statistics import levenshtein
from gecco.gecco import Module

class LexiconModule(Module):
    UNIT = folia.Word

    def verifysettings(self):
        super().verifysettings()

        if 'delimiter' not in self.settings:
            self.settings['delimiter'] = "\t"
        elif self.settings['delimiter'].lower() == 'space':
            self.settings['delimiter'] = " "
        elif self.settings['delimiter'].lower() == 'tab':
            self.settings['delimiter'] = "\t"
        if 'reversedformat' not in self.settings: #reverse format has (word,freq) pairs rather than (freq,word) pairs
            self.settings['reversedformat'] = False

        if not self.settings['maxdistance']:
            self.settings['maxdistance'] = 2
        if not self.settings['medld']:
            self.settings['medld'] = 1
        if not self.settings['maxlength']:
            self.settings['maxlength'] = 15 #longer words will be ignored
        if not self.settings['minlength']:
            self.settings['minlength'] = 5 #shorter word will be ignored
        if not self.settings['minfreqthreshold']:
            self.settings['minfreqthreshold'] = 10000
        if not self.settings['maxnrclosest']:
            self.settings['maxnrclosest'] = 5

        if not self.settings['cachesize']:
            self.settings['cachesize'] = 1000

        if not self.settings['suffixes']:
            self.settings['suffixes'] = []
        if not self.settings['prefixes']:
            self.settings['prefixes'] = []

    def load(self):
        """Load the requested modules from self.models"""
        self.lexicon = {}
        self._cache = collections.OrderedDict()

        if not self.models:
            raise Exception("Specify one or more models to load!")

        for modelfile in self.models:
            if not os.path.exists(modelfile):
                raise IOError("Missing expected model file:" + modelfile)
            self.log("Loading model file " + modelfile)
            with open(modelfile,'r',encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        fields = [ x.strip() for x in line.split(self.settings['delimiter']) ]
                        if  len(fields) != 2:
                            raise Exception("Syntax error in " + modelfile + ", expected two items, got " + str(len(fields)))

                        if self.settings['reversedformat']:
                            freq, word = fields
                        else:
                            word, freq = fields
                        freq = int(freq)

                        if freq > self.settings['minfreqthreshold']:
                            self.lexicon[word] = freq


    def findclosest(self, word):
        l = len(word)
        if self._cache and word in self._cache:
            return self._cache[word]
        elif l < self.settings['minlength'] or l > self.settings['maxlength']:
            #word too long or too short, ignore
            return False
        elif word in self.lexicon:
            #word is in lexicon, no need to find suggestions
            return False
        else:
            #word is not in lexicon

            #but first try to strip known suffixes and prefixes and try again
            for suffix in self.settings['suffixes']:
                if word.endswith(suffix):
                    if word[:-len(suffix)] in self.lexicon:
                        return False
            for prefix in self.settings['prefixes']:
                if word.endswith(prefix):
                    if word[len(prefix):] in self.lexicon:
                        return False

            #ok, not found, let's find closest matches by levenshtein distance

            results = []
            for key, freq in self.lexicon:
                ld = levenshtein(word, key, self.settings['maxdistance'])
                if ld <= self.settings['maxdistance']:
                    self.results.append( (key, ld) )

            results.sort(key=lambda x: x[1])[:self.settings['maxnrclosest']]
            if self.settings['cachesize'] > 0:
                self.cache(word,results)
            return results


    def cache(self, word, results):
        if len(self._cache) == self.settings['cachesize']:
            self._cache.popitem(False)
        self._cache[word] = results

    def run(self, word, lock, **parameters):
        """This method gets invoked by the Corrector when it runs locally. word is a folia.Word instance"""
        wordstr = str(word)
        results = self.findclosest(wordstr)
        if results:
            self.addwordsuggestions(lock, word, [ result for result,distance in results ] )

    def runclient(self, client, word, lock, **parameters):
        """This method gets invoked by the Corrector when it should connect to a remote server, the client instance is passed and already available (will connect on first communication). word is a folia.Word instance"""
        wordstr = str(word)
        results = json.loads(client.communicate(wordstr))
        if results:
            self.addwordsuggestions(lock, word, [ result for result,distance in results ] )

    def server_handler(self, word):
        """This methods gets called by the module's server and handles a message by the client. The return value (str) is returned to the client"""
        return json.dumps(self.findclosest(word))